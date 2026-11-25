# Run on this Windows PC after the Vast.ai instance is up (leave the window open).
# One SSH connection forwards all three model servers, reconnecting if it drops:
#   localhost:8001 -> GPU :8000  chat / router
#   localhost:8002 -> GPU :8010  embeddings
#   localhost:8003 -> GPU :8020  reranker
# Ports are bound to 127.0.0.1 only (not your LAN). Docker containers still reach them as
# host.docker.internal:800x, so the URLs in .env never change between rentals.
#
# Usage:  powershell -ExecutionPolicy Bypass -File gpu\tunnel.ps1 [-HostIp 1.2.3.4] [-SshPort 12345]
param(
    [string]$HostIp,
    [int]$SshPort,
    [string]$User = "root",
    [string]$KeyFile = "$env:USERPROFILE\.ssh\id_ed25519"
)

if (-not $HostIp)  { $HostIp  = Read-Host "Vast instance IP (Vast console -> Connect -> direct SSH)" }
if (-not $SshPort) { $SshPort = [int](Read-Host "Vast SSH port") }

$forwards = @(
    "-L", "127.0.0.1:8001:127.0.0.1:8000",
    "-L", "127.0.0.1:8002:127.0.0.1:8010",
    "-L", "127.0.0.1:8003:127.0.0.1:8020"
)

Write-Host "Tunnel to $User@$HostIp (ssh port $SshPort): chat :8001, embed :8002, rerank :8003. Ctrl+C to stop."
while ($true) {
    ssh -N `
        -o ServerAliveInterval=15 -o ServerAliveCountMax=3 `
        -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new `
        -i $KeyFile -p $SshPort @forwards "$User@$HostIp"
    Write-Host "Tunnel dropped (exit $LASTEXITCODE). Reconnecting in 5 s..."
    Start-Sleep -Seconds 5
}
