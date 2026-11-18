import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import scriptsync_gui as g
T = sys.argv[1]; shot = sys.argv[2]
app = g.App()
app.update()
app.add_clips = None
app._add_entry(os.path.join(T, "testclip.mp4"), os.path.join(T, "testclip.txt"))
app._add_entry(os.path.join(T, "other.mp4"), None)      # no script -> should be flagged, not run
app._after_add(); app.update()
pass
app.var_out.set("")
app.start()
t0 = time.time()
while app._busy() or not app.q.empty():
    app.update(); time.sleep(0.02)
    if time.time() - t0 > 60: print("TIMEOUT"); break
for _ in range(20): app.update(); time.sleep(0.02)
first = next(iter(app.entries))
app.tree.selection_set(first); app.update()
for _ in range(10): app.update(); time.sleep(0.02)
rows = [app.tree.item(i, "values") for i in app.tree.get_children()]
print("rows:", rows)
n_cues = len(app.ctree.get_children())
print("cues shown:", n_cues, "|", app.summary.cget("text"))
# a second sync in the same session (users reported having to restart the app between sessions)
def srts():                                     # a sync writes its working .srt to the app's folder, not to T
    d = g.core.working_dir()
    return sorted(f for f in os.listdir(d) if f.endswith(".srt") and "_synced_" in f) if os.path.isdir(d) else []
before = len(srts())
app.start()
t0 = time.time()
for _ in range(5): app.update(); time.sleep(0.02)
while app._busy() or not app.q.empty():
    app.update(); time.sleep(0.02)
    if time.time() - t0 > 60: print("TIMEOUT (second sync)"); break
for _ in range(20): app.update(); time.sleep(0.02)
rows2 = [app.tree.item(i, "values") for i in app.tree.get_children()]
after = len(srts())
print("second sync in the same session:", rows2[0][:2], f"| subtitle files {before} -> {after}")
# nothing lands beside the clip until Save as .srt
left = [f for f in os.listdir(T) if "_synced_" in f or f.endswith(".review.txt")]
save_text = app.btn_save_cut.cget("text")
app.tree.selection_set(first); app.update()
saved = os.path.join(T, "saved_copy.srt")
app.save_srt(dest=saved); app.update()
print("save button:", save_text, "| saved:", os.path.exists(saved))
import shutil
if shutil.which("import"):                     # ImageMagick, for a screenshot to look at
    os.system(f"import -window root {shot}")
app.destroy()
# fail loudly (CI): the clip with a script synced and shows subtitles; the one without was not run
assert any("Done" in " ".join(r) for r in rows), f"the clip with a script didn't finish: {rows}"
assert any("Needs a script" in " ".join(r) for r in rows), f"the clip without a script should wait: {rows}"
assert n_cues > 0, "no subtitles shown in the Review list"
assert any("Done" in " ".join(r) for r in rows2), f"a second sync in the same session didn't finish: {rows2}"
assert after == before + 1, f"a second sync in the same session wrote no new subtitles ({before} -> {after})"
assert not left, f"a sync wrote files beside the clip: {left}"
assert save_text == "Save as .srt", save_text
assert os.path.exists(saved) and os.path.getsize(saved) > 0, "Save as .srt wrote nothing"
print("GUI smoke test OK")
