import { useEffect, useState } from "react";

/* AN IMAGE BEHIND THE SESSION, for an <img>.

   A colleague's photo is served only to somebody signed in to the workspace, and an <img> sends no
   Authorization header -- so `<img src={url}>` is a 401 every time, and the directory's photos
   could never appear. This fetches the bytes with the caller's own session and hands back an
   object URL for the <img>, released when the component goes or the path changes.

   `fetchBlob` is the app's own authenticated fetch (the portal's getBlob, the console's). Pass a
   module-level function: a new function on every render would fetch on every render. */
export function useAuthedImage(path, fetchBlob) {
  const [src, setSrc] = useState(null);

  useEffect(() => {
    if (!path || !fetchBlob) {
      setSrc(null);
      return undefined;
    }
    let alive = true;
    let objectUrl = null;
    fetchBlob(path)
      .then((blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (alive) setSrc(null);
      });
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path, fetchBlob]);

  return src;
}
