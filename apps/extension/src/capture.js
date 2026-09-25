// A page is selected only after an extension action and a visible save gesture.
// Never transmit the tab's path, query, fragment, DOM, or browsing history.
export function pageSelection(tab) {
  if (!tab?.url) throw new Error("Open NavoX on an HTTPS page to save it.");
  if (!tab.title) throw new Error("This page needs a short title.");
  let url;
  try {
    url = new URL(tab.url);
  } catch {
    throw new Error("This page cannot be saved.");
  }
  if (url.protocol !== "https:" || !url.hostname || url.username || url.password) {
    throw new Error("Only HTTPS pages without embedded credentials can be saved.");
  }
  const title = tab.title.trim();
  if (!title || title.length > 200) throw new Error("This page needs a short title.");
  return { origin: url.origin, title };
}

export function pageNoteImport(selection, note) {
  if (!selection || typeof note !== "string") throw new Error("Write a note before saving.");
  const safe = pageSelection({ url: selection.origin, title: selection.title });
  const description = note.trim();
  if (!description || description.length > 2000) {
    throw new Error("Write a note of at most 2,000 characters before saving.");
  }
  return {
    format: "json",
    content: JSON.stringify([{
      id: safe.origin,
      title: safe.title,
      description,
      url: safe.origin,
    }]),
    name: `Page note: ${new URL(safe.origin).hostname.slice(0, 100)}`,
  };
}
