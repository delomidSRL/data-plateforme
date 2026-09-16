// Trigger a browser "Save as" for an in-memory Blob (e.g. a CSV fetched with the bearer
// token via api/client.apiDownload). Revokes the object URL after the click so the blob
// can be garbage-collected.
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
