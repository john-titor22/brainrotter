# Brainrotter — Chrome extension

A toolbar button that opens the Brainrotter dashboard and shows the render queue
at a glance (badge = jobs rendering + waiting).

> The extension is **optional**. It can't start the server — use the
> **Brainrotter** desktop shortcut (or `brainrotter app`) for that. The extension
> is a convenience badge + quick controls for when a normal browser window is
> already open.

## Install (one time)

1. Have the dashboard running (open the Brainrotter app, or `brainrotter serve`).
2. Open `chrome://extensions` in Chrome (or Edge / Brave).
3. Turn on **Developer mode** (top-right toggle).
4. Click **Load unpacked** and pick this `extension/` folder.
5. Pin the Brainrotter icon: click the puzzle-piece in the toolbar → pin.

Click the icon any time:
- **Popup** shows queue status with Pause / Clear buttons.
- **Open dashboard** focuses the dashboard tab (or opens one).
- The **badge** shows how many videos are rendering + queued (purple = running,
  grey = paused, none = idle or dashboard offline).

## Different port / host?

The extension talks to `http://127.0.0.1:8000` by default. To change it, open the
extension's service worker console (`chrome://extensions` → Brainrotter →
"service worker") and run:

```js
chrome.storage.sync.set({ dashboardUrl: "http://localhost:9000" })
```

## Prefer an installable app instead of an extension?

The dashboard is also a PWA. With it open in Chrome, use the **install icon** in
the address bar (or ⋮ → *Install Brainrotter*) to get it as a standalone app
window with its own icon in the Start menu / taskbar — no extension needed.
