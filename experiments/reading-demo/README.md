# Reading state demo

This directory contains screenshots of the second feature:

- `reading-home-first.png`: first homepage visit, showing recent/unread/star/recent-opened;
- `reading-document.png`: a document page with read/star controls after auto-read;
- `reading-home-after.png`: homepage after opening a document, showing read count and
  recent-opened.

State is stored in the browser's `localStorage`, keyed by project id.  This is
only a demo capture; the implementation lives in the main NoteManager tree.
