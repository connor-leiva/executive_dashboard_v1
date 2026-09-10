import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { useCallback, useEffect, useRef, useState } from "react";

import { usePostLessonImage } from "./queries.js";

/* The lesson body editor.
 *
 * TIPTAP, NOT contentEditable + execCommand. The design prototype demonstrates the interaction
 * with execCommand because that is the shortest way to show it in a static file; shipping it
 * would mean storing whatever six browsers each decide `<b>` means, and cleaning that up on the
 * server forever. ProseMirror holds a real document with a schema, so the editor can only make
 * shapes the sanitizer already allows.
 *
 * THE SCHEMA IS THE SAME ALLOWLIST AS THE SERVER'S, deliberately: p, h2, h3, strong, em, a, ul,
 * ol, li, blockquote, br, hr, img, figure, figcaption. Everything else is switched off below. It
 * is still not a trust boundary -- anything can POST to the API and services/lesson_richtext.py
 * is what actually decides -- but an author should never type something that silently disappears
 * when they save.
 *
 * DEBOUNCED SEPARATELY FROM EVERY OTHER FIELD, at 1200ms rather than the 700ms the rest of the
 * form uses. A rich-text document in the same payload as a title means every keystroke ships the
 * whole article; splitting it also means a slow save of the body never delays a title.
 */

const BODY_SAVE_MS = 1200;
const WORDS_PER_MINUTE = 220;   // must match services/lesson_richtext.WORDS_PER_MINUTE

function countWords(editor) {
  const text = editor?.getText({ blockSeparator: " " }) || "";
  const words = text.split(/\s+/).filter(Boolean).length;
  return { words, minutes: words ? Math.max(1, Math.round(words / WORDS_PER_MINUTE)) : 0 };
}

function ToolbarButton({ on, onClick, title, children, wide }) {
  return (
    <button
      type="button"
      className={`cb-rte-btn${on ? " on" : ""}${wide ? " wide" : ""}`}
      title={title}
      aria-label={title}
      aria-pressed={on ? "true" : "false"}
      /* The click must not take the selection with it. Without this the caret is gone by the
         time the handler runs and "make this bold" has nothing to make bold. */
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export default function LessonBody({ courseId, lessonId, value, onChange, onCounts }) {
  const upload = usePostLessonImage();
  const fileInput = useRef(null);
  const timer = useRef(null);
  const latest = useRef(value || "");
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkUrl, setLinkUrl] = useState("");
  const [imageOpen, setImageOpen] = useState(false);
  const [alt, setAlt] = useState("");
  const [error, setError] = useState(null);
  const [counts, setCounts] = useState({ words: 0, minutes: 0 });

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        // Everything the sanitizer would strip anyway. Switched off here so the author never
        // types a heading level or a code block that vanishes on save.
        heading: { levels: [2, 3] },
        codeBlock: false,
        code: false,
        strike: false,
        horizontalRule: {},
        blockquote: {},
      }),
      Link.configure({
        openOnClick: false,          // clicking a link in the EDITOR should place the caret
        autolink: true,
        protocols: ["http", "https", "mailto"],
      }),
      Image.configure({ inline: false, allowBase64: false }),
      // Without this an unwritten lesson is a blank white rectangle with no sign it is an
      // editor at all. The CSS for it was already there; the extension was not.
      Placeholder.configure({ placeholder: "Write the lesson…" }),
    ],
    content: value || "",
    onUpdate: ({ editor: ed }) => {
      const html = ed.isEmpty ? "" : ed.getHTML();
      latest.current = html;
      const next = countWords(ed);
      setCounts(next);
      onCounts?.(next);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => onChange(latest.current), BODY_SAVE_MS);
    },
  });

  /* Resync only when the LESSON changes, never on every render of the same one. Pushing the
     server's copy back in mid-sentence would move the cursor and drop whatever was typed inside
     the debounce window -- the classic autosave bug, and worse here because it is a document. */
  useEffect(() => {
    if (!editor) return;
    editor.commands.setContent(value || "", false);
    latest.current = value || "";
    const next = countWords(editor);
    setCounts(next);
    onCounts?.(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, lessonId]);

  useEffect(() => () => clearTimeout(timer.current), []);

  /* Commit anything pending. The parent calls this on blur and on Done, so closing the lesson
     inside the debounce window does not throw the last paragraph away. */
  const flush = useCallback(() => {
    if (!timer.current) return;
    clearTimeout(timer.current);
    timer.current = null;
    onChange(latest.current);
  }, [onChange]);

  function openLinkBar() {
    if (!editor) return;
    setLinkUrl(editor.getAttributes("link").href || "");
    setImageOpen(false);
    setLinkOpen(true);
  }

  function applyLink() {
    const href = linkUrl.trim();
    if (!href) return removeLink();
    editor.chain().focus().extendMarkRange("link").setLink({ href }).run();
    setLinkOpen(false);
  }

  function removeLink() {
    editor.chain().focus().extendMarkRange("link").unsetLink().run();
    setLinkOpen(false);
  }

  async function insertImage(file) {
    setError(null);
    // ALT IS REQUIRED AND THE INSERT IS BLOCKED WITHOUT IT. The server refuses too, but the
    // person who cannot see an unlabelled image is the person who cannot report it missing, so
    // the ask happens here where somebody can still answer it.
    if (!alt.trim()) {
      setError("Describe the image for anyone who cannot see it.");
      return;
    }
    try {
      const out = await upload.mutateAsync({
        courseId, lessonId, fields: { alt: alt.trim(), file },
      });
      // The URL THE SERVER RETURNED, never one assembled here. What is stored is the storage
      // key -- a signed URL would expire and a body is kept for years -- and only the server
      // knows which route serves it back. Building one in the browser is how this shipped
      // pointing at a route that does not exist, so the image 404'd and then vanished on save.
      editor.chain().focus().setImage({ src: out.url, alt: alt.trim() }).run();
      setImageOpen(false);
      setAlt("");
    } catch (err) {
      setError(err?.message || "That image could not be uploaded.");
    }
  }

  if (!editor) return <div className="cb-rte-shell" />;

  const is = (name, attrs) => editor.isActive(name, attrs);

  return (
    <div className="cb-rte-shell" onBlur={(e) => {
      if (!e.currentTarget.contains(e.relatedTarget)) flush();
    }}>
      <div className="cb-rte-bar">
        <ToolbarButton title="Bold" on={is("bold")}
                       onClick={() => editor.chain().focus().toggleBold().run()}>
          <strong>B</strong>
        </ToolbarButton>
        <ToolbarButton title="Italic" on={is("italic")}
                       onClick={() => editor.chain().focus().toggleItalic().run()}>
          <em>I</em>
        </ToolbarButton>

        <span className="cb-rte-div" aria-hidden="true" />

        <ToolbarButton title="Heading" on={is("heading", { level: 2 })}
                       onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>
          H2
        </ToolbarButton>
        <ToolbarButton title="Subheading" on={is("heading", { level: 3 })}
                       onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}>
          H3
        </ToolbarButton>
        <ToolbarButton title="Body text" on={is("paragraph")} wide
                       onClick={() => editor.chain().focus().setParagraph().run()}>
          Body
        </ToolbarButton>

        <span className="cb-rte-div" aria-hidden="true" />

        <ToolbarButton title="Bulleted list" on={is("bulletList")}
                       onClick={() => editor.chain().focus().toggleBulletList().run()}>
          •
        </ToolbarButton>
        <ToolbarButton title="Numbered list" on={is("orderedList")}
                       onClick={() => editor.chain().focus().toggleOrderedList().run()}>
          1.
        </ToolbarButton>
        <ToolbarButton title="Callout" on={is("blockquote")}
                       onClick={() => editor.chain().focus().toggleBlockquote().run()}>
          ❝
        </ToolbarButton>

        <span className="cb-rte-div" aria-hidden="true" />

        <ToolbarButton title="Link" on={is("link")} wide onClick={openLinkBar}>Link</ToolbarButton>
        <ToolbarButton title="Image" wide
                       onClick={() => { setLinkOpen(false); setImageOpen(true); }}>
          Image
        </ToolbarButton>
        <ToolbarButton title="Clear formatting"
                       onClick={() => editor.chain().focus().unsetAllMarks().setParagraph().run()}>
          ⌫
        </ToolbarButton>

        <span className="cb-rte-count">
          {counts.words.toLocaleString()} {counts.words === 1 ? "word" : "words"}
          {counts.minutes ? ` · about ${counts.minutes} min read` : ""}
        </span>
      </div>

      {/* An inline bar, not window.prompt: a prompt loses the selection, cannot show the href
          that is already there, and has no Remove. */}
      {linkOpen ? (
        <div className="cb-rte-sub">
          <input
            autoFocus
            value={linkUrl}
            placeholder="https://… or /sops"
            onChange={(e) => setLinkUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); applyLink(); }
              if (e.key === "Escape") setLinkOpen(false);
            }}
          />
          <button type="button" className="cb-rte-apply" onClick={applyLink}>Apply</button>
          <button type="button" onClick={removeLink}>Remove</button>
          <button type="button" onClick={() => setLinkOpen(false)}>Cancel</button>
        </div>
      ) : null}

      {imageOpen ? (
        <div className="cb-rte-sub">
          <input
            autoFocus
            value={alt}
            placeholder="Describe the image (required)"
            onChange={(e) => { setAlt(e.target.value); setError(null); }}
          />
          <button type="button" className="cb-rte-apply"
                  disabled={upload.isPending}
                  onClick={() => fileInput.current?.click()}>
            {upload.isPending ? "Uploading…" : "Choose image"}
          </button>
          <button type="button" onClick={() => { setImageOpen(false); setError(null); }}>
            Cancel
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            style={{ display: "none" }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) insertImage(file);
            }}
          />
        </div>
      ) : null}

      {error ? <div className="cb-rte-error">{error}</div> : null}

      <EditorContent className="cb-rte" editor={editor} />
    </div>
  );
}
