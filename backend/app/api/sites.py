"""Site management: add / list / update / delete sites, trigger crawls, inspect pages and jobs."""

from datetime import datetime
from typing import Annotated, Literal

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crawler.ssrf import UnsafeURLError, assert_public_url
from app.crawler.urls import canonicalize
from app.db.models import Chunk, CrawlJob, Page, Site
from app.db.session import get_session

router = APIRouter(prefix="/sites", tags=["sites"])

DB = Annotated[AsyncSession, Depends(get_session)]


def get_arq(request: Request) -> ArqRedis:
    return request.app.state.arq


Queue = Annotated[ArqRedis, Depends(get_arq)]


# ---- schemas -------------------------------------------------------------------------------


class SiteSettings(BaseModel):
    name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=2000)
    enabled: bool | None = None
    render_mode: Literal["auto", "static", "browser"] | None = None
    include_patterns: list[str] | None = Field(None, max_length=50)
    exclude_patterns: list[str] | None = Field(None, max_length=50)
    max_depth: int | None = Field(None, ge=0, le=10)
    max_pages: int | None = Field(None, ge=1, le=5000)
    freshness_max_age_sec: int | None = Field(None, ge=0, le=30 * 86400)
    respect_robots: bool | None = None
    crawl_delay_ms: int | None = Field(None, ge=0, le=30_000)
    # e.g. https://shop.com/search?q={query}  ("" clears it)
    site_search_url: str | None = Field(None, max_length=2000)

    @field_validator("site_search_url")
    @classmethod
    def search_template(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return v if v is None else ""
        v = v.strip()
        if "{query}" not in v:
            raise ValueError("the site search URL must contain {query}")
        if canonicalize(v.replace("{query}", "test")) is None:
            raise ValueError("not a valid http(s) URL")
        return v


class SiteCreate(SiteSettings):
    root_url: str = Field(min_length=4, max_length=2000)
    crawl_now: bool = True

    @field_validator("root_url")
    @classmethod
    def normalize(cls, v: str) -> str:
        v = v.strip()
        if "://" not in v:
            v = "https://" + v
        url = canonicalize(v)
        if url is None:
            raise ValueError("not a valid http(s) URL")
        return url


class JobOut(BaseModel):
    id: int
    type: str
    status: str
    pages_seen: int
    pages_changed: int
    pages_unchanged: int
    pages_removed: int
    pages_failed: int
    errors: list
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class SiteOut(BaseModel):
    id: int
    name: str
    root_url: str
    description: str | None
    enabled: bool
    render_mode: str
    include_patterns: list[str]
    exclude_patterns: list[str]
    max_depth: int
    max_pages: int
    freshness_max_age_sec: int
    respect_robots: bool
    crawl_delay_ms: int
    site_search_url: str | None = None
    created_at: datetime
    # stats
    pages: int = 0
    pages_gone: int = 0
    chunks: int = 0
    chunks_embedded: int = 0
    last_fetched_at: datetime | None = None
    last_job: JobOut | None = None

    model_config = {"from_attributes": True}


class PageOut(BaseModel):
    id: int
    url: str
    title: str | None
    status: str
    http_status: int | None
    version: int
    fetched_at: datetime | None
    changed_at: datetime | None
    chunks: int
    chunks_embedded: int


# ---- helpers -------------------------------------------------------------------------------


async def site_stats(db: AsyncSession, site_ids: list[int]) -> dict[int, dict]:
    stats: dict[int, dict] = {i: {} for i in site_ids}
    if not site_ids:
        return stats
    page_rows = await db.execute(
        select(
            Page.site_id,
            func.count().filter(Page.status == "active"),
            func.count().filter(Page.status == "gone"),
            func.max(Page.fetched_at),
        ).where(Page.site_id.in_(site_ids)).group_by(Page.site_id)
    )
    for sid, active, gone, last in page_rows:
        stats[sid].update(pages=active, pages_gone=gone, last_fetched_at=last)
    chunk_rows = await db.execute(
        select(
            Page.site_id,
            func.count(Chunk.id),
            func.sum(case((Chunk.embedding.is_not(None), 1), else_=0)),
        )
        .join(Page, Page.id == Chunk.page_id)
        .where(Page.site_id.in_(site_ids), Page.status == "active",
               Chunk.page_version == Page.version)
        .group_by(Page.site_id)
    )
    for sid, total, embedded in chunk_rows:
        stats[sid].update(chunks=total, chunks_embedded=int(embedded or 0))
    latest = (
        select(CrawlJob.site_id, func.max(CrawlJob.id).label("job_id"))
        .where(CrawlJob.site_id.in_(site_ids)).group_by(CrawlJob.site_id).subquery()
    )
    for job in (await db.scalars(select(CrawlJob).join(latest, CrawlJob.id == latest.c.job_id))):
        stats[job.site_id]["last_job"] = JobOut.model_validate(job)
    return stats


async def to_out(db: AsyncSession, sites: list[Site]) -> list[SiteOut]:
    stats = await site_stats(db, [s.id for s in sites])
    return [SiteOut.model_validate(s).model_copy(update=stats[s.id]) for s in sites]


async def get_site_or_404(db: AsyncSession, site_id: int) -> Site:
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site not found")
    return site


async def start_crawl(db: AsyncSession, queue: ArqRedis, site: Site) -> CrawlJob:
    await db.execute(select(Site.id).where(Site.id == site.id).with_for_update())
    running = await db.scalar(
        select(CrawlJob).where(CrawlJob.site_id == site.id,
                               CrawlJob.status.in_(("queued", "running")))
    )
    if running is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a crawl is already queued or running")
    job = CrawlJob(site_id=site.id, type="full", status="queued", errors=[])
    db.add(job)
    await db.commit()
    await db.refresh(job)
    try:
        await queue.enqueue_job("crawl_site_task", site.id, job.id, _job_id=f"crawl:{job.id}")
    except (RedisError, OSError):
        # The committed job is durable. The scheduler retries delivery by the same ID.
        pass
    return job


# ---- routes --------------------------------------------------------------------------------


@router.get("", response_model=list[SiteOut])
async def list_sites(db: DB):
    sites = list(await db.scalars(select(Site).order_by(Site.created_at)))
    return await to_out(db, sites)


@router.post("", response_model=SiteOut, status_code=status.HTTP_201_CREATED)
async def create_site(body: SiteCreate, db: DB, queue: Queue):
    try:
        await assert_public_url(body.root_url, allow_private=get_settings().allow_private_urls)
    except UnsafeURLError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    values = body.model_dump(exclude={"root_url", "crawl_now"}, exclude_none=True)
    host = body.root_url.split("://", 1)[1].split("/", 1)[0]
    values.setdefault("name", host.removeprefix("www."))
    site = Site(root_url=body.root_url, **values)
    db.add(site)
    try:
        await db.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "this site is already added") from exc
    await db.refresh(site)
    if body.crawl_now:
        await start_crawl(db, queue, site)
    return (await to_out(db, [site]))[0]


@router.get("/{site_id}", response_model=SiteOut)
async def get_site(site_id: int, db: DB):
    return (await to_out(db, [await get_site_or_404(db, site_id)]))[0]


@router.patch("/{site_id}", response_model=SiteOut)
async def update_site(site_id: int, body: SiteSettings, db: DB):
    site = await get_site_or_404(db, site_id)
    for k, v in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(site, k, (v or None) if k == "site_search_url" else v)
    await db.commit()
    await db.refresh(site)
    return (await to_out(db, [site]))[0]


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(site_id: int, db: DB):
    site = await get_site_or_404(db, site_id)
    await db.delete(site)  # pages, chunks (+ vectors) and jobs cascade
    await db.commit()


@router.post("/{site_id}/crawl", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def crawl_site(site_id: int, db: DB, queue: Queue):
    return await start_crawl(db, queue, await get_site_or_404(db, site_id))


@router.get("/{site_id}/jobs", response_model=list[JobOut])
async def list_jobs(site_id: int, db: DB, limit: int = Query(20, ge=1, le=100)):
    await get_site_or_404(db, site_id)
    return list(await db.scalars(
        select(CrawlJob).where(CrawlJob.site_id == site_id)
        .order_by(CrawlJob.id.desc()).limit(limit)
    ))


@router.get("/{site_id}/pages", response_model=list[PageOut])
async def list_pages(
    site_id: int, db: DB,
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=200),
):
    await get_site_or_404(db, site_id)
    chunks = (
        select(
            Chunk.page_id,
            func.count().label("n"),
            func.sum(case((Chunk.embedding.is_not(None), 1), else_=0)).label("e"),
        )
        .join(Page, Page.id == Chunk.page_id)
        .where(Page.site_id == site_id, Chunk.page_version == Page.version)
        .group_by(Chunk.page_id).subquery()
    )
    stmt = (
        select(Page, func.coalesce(chunks.c.n, 0), func.coalesce(chunks.c.e, 0))
        .outerjoin(chunks, chunks.c.page_id == Page.id)
        .where(Page.site_id == site_id)
        .order_by(Page.status, Page.canonical_url).limit(limit).offset(offset)
    )
    if q:
        stmt = stmt.where(Page.canonical_url.ilike(f"%{q}%") | Page.title.ilike(f"%{q}%"))
    return [
        PageOut(id=p.id, url=p.url, title=p.title, status=p.status, http_status=p.http_status,
                version=p.version, fetched_at=p.fetched_at, changed_at=p.changed_at,
                chunks=int(n), chunks_embedded=int(e))
        for p, n, e in await db.execute(stmt)
    ]
