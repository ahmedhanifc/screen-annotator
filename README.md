# screen-annotator

Draw on top of your live screen with a pen, a highlighter, or text. The window is
click-through, so whatever is under the ink stays clickable and scrollable. Scroll the
page and the ink wipes itself, so you always get a blank canvas.

Runs on Linux (X11) and Windows. MIT licensed.

## Install

Needs Python 3.9 or newer.

```bash
git clone https://github.com/ahmedhanifc/screen-annotator.git
cd screen-annotator
pip install .
```

## Run

```bash
screen-annotator
```

* while the overlay is up:
  * left-drag draws
  * scroll clears the ink (the page below still scrolls)
  * `Enter` copies the screen plus the ink to the clipboard
  * `Esc` quits
* the toolbar at the top holds the rest: tools, colours, sizes
* to put it on a hotkey, point a keyboard shortcut at `overlay.sh` in this repo
  * it starts the overlay, or quits it if it is already running
  * on Windows, use an [AutoHotkey](https://www.autohotkey.com/) line: `#a::Run, screen-annotator`

## Credits

* inspired by [Satty](https://github.com/gabm/Satty), which annotates a frozen screenshot
  instead of the live screen
* [MIT](LICENSE) © Ahmed Hanif
