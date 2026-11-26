// The chat page is always mounted, so it reports the open conversation's title here and the
// app combines it with the current page to form the browser tab title.
let chatTitle = "";
export const CHAT_TITLE_EVENT = "smish:chat-title";
export const getChatTitle = () => chatTitle;
export function setChatTitle(title: string) {
  if (title === chatTitle) return;
  chatTitle = title;
  window.dispatchEvent(new Event(CHAT_TITLE_EVENT));
}
