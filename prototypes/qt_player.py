"""Prototype, not part of the app: play a clip with its .srt drawn on top, using Qt's media player.

It answers three questions before the app's window is moved from tkinter to Qt:
  1. Does the picture, the sound and the subtitle stay in step, to the frame, for the whole clip?
  2. Does PySide6 install with pip and run on Windows (no blocked DLLs)?
  3. Does a 1080x1920 phone clip play and step smoothly on the CPU?

    pip install PySide6
    python prototypes/qt_player.py clip.mp4 clip.srt              # the player window
    python prototypes/qt_player.py clip.mp4 clip.srt --selftest   # measure, print numbers, quit

The subtitle shown is chosen from the timestamp of the video frame on screen (QVideoFrame.startTime),
not from a separate clock, so it can't drift from the picture. Qt keeps the picture in step with the sound.
"""
import argparse
import os
import re
import statistics
import sys
import time

from PySide6.QtCore import QEventLoop, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoSink
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (QApplication, QComboBox, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel, QSlider,
                               QStyle, QToolButton, QVBoxLayout, QWidget)

_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def read_srt(path):
    """-> [(start_s, end_s, text)]"""
    cues = []
    for block in re.split(r"\n\s*\n", open(path, encoding="utf-8-sig").read().strip()):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            m = _TIME.search(line)
            if m:
                g = [int(x) for x in m.groups()]
                s = g[0] * 3600 + g[1] * 60 + g[2] + int(m.group(4).ljust(3, "0")) / 1000
                e = g[4] * 3600 + g[5] * 60 + g[6] + int(m.group(8).ljust(3, "0")) / 1000
                cues.append((s, e, "\n".join(lines[i + 1:])))
                break
    return cues


def cue_at(cues, t):
    for i, (s, e, _) in enumerate(cues):
        if s <= t < e:
            return i
    return -1


def timecode(t, fps):
    """HH:MM:SS:FF, the frame counted within the second (as an editor shows non-drop-frame time)."""
    return "%02d:%02d:%02d:%02d" % (int(t // 3600), int(t // 60) % 60, int(t) % 60, int((t % 1) * fps + 1e-6))


def frame_rate(player, fallback=29.97):
    v = player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
    try:
        return float(v) if v and float(v) > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def wait_for(signal, timeout_ms=3000, pred=lambda *a: True):
    """Run the event loop until ``signal`` fires with arguments that satisfy ``pred``; -> the arguments."""
    loop, got = QEventLoop(), []

    def hit(*a):
        if pred(*a):
            got.append(a)
            loop.quit()
    signal.connect(hit)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    signal.disconnect(hit)
    return got[0] if got else None


def load(player, path):
    player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
    ok = (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia)
    if player.mediaStatus() not in ok:
        wait_for(player.mediaStatusChanged, 10000, lambda st: st in ok)
    return player.mediaStatus() in ok


# --------------------------------------------------------------------------- the window
class Player(QWidget):
    def __init__(self, clip, cues):
        super().__init__()
        self.cues, self.fps, self.t, self.loop_cue, self.cur = cues, 29.97, 0.0, False, -1
        self.setWindowTitle("Qt player prototype - " + os.path.basename(clip))
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)

        self.scene = QGraphicsScene(self)
        self.video = QGraphicsVideoItem()
        self.scene.addItem(self.video)
        self.box = QGraphicsRectItem()
        self.box.setBrush(QBrush(QColor(0, 0, 0, 160)))
        self.box.setPen(QPen(Qt.PenStyle.NoPen))
        self.scene.addItem(self.box)
        self.text = QGraphicsSimpleTextItem()
        self.text.setBrush(QBrush(QColor("white")))
        self.scene.addItem(self.text)
        self.view = QGraphicsView(self.scene)
        self.view.setStyleSheet("background: #2c2c2a; border: 0")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.player.setVideoOutput(self.video)
        self.video.videoSink().videoFrameChanged.connect(self.on_frame)
        self.video.nativeSizeChanged.connect(lambda _s: self.fit())

        self.tc = QLabel("00:00:00:00")
        self.tc.setStyleSheet("font: bold 15px Consolas; color: #a35a12")
        self.info = QLabel("")
        self.info.setStyleSheet("color: #5f5f5f")
        self.bar = QSlider(Qt.Orientation.Horizontal)
        self.bar.sliderMoved.connect(lambda ms: self.player.setPosition(ms))
        self.player.durationChanged.connect(lambda d: self.bar.setRange(0, d))
        st = self.style()

        def button(icon, tip, fn, key):
            b = QToolButton()
            b.setIcon(st.standardIcon(icon))
            b.setToolTip(f"{tip} ({key})")
            b.setAutoRaise(True)
            b.clicked.connect(fn)
            QShortcut(QKeySequence(key), self, activated=fn)
            return b
        SP = QStyle.StandardPixmap
        self.b_play = button(SP.SP_MediaPlay, "Play / pause", self.toggle, "Space")
        row = QHBoxLayout()
        for w in (button(SP.SP_MediaSkipBackward, "Previous subtitle", lambda: self.jump(-1), "Up"),
                  button(SP.SP_MediaSeekBackward, "Back 1 frame", lambda: self.step(-1), "Left"),
                  self.b_play,
                  button(SP.SP_MediaSeekForward, "Forward 1 frame", lambda: self.step(1), "Right"),
                  button(SP.SP_MediaSkipForward, "Next subtitle", lambda: self.jump(1), "Down")):
            row.addWidget(w)
        self.b_loop = button(SP.SP_BrowserReload, "Loop the current subtitle", self.toggle_loop, "L")
        self.b_loop.setCheckable(True)
        row.addWidget(self.b_loop)
        speed = QComboBox()
        speed.addItems(["1x", "0.75x", "0.5x"])
        speed.setToolTip("Playback speed")
        speed.currentTextChanged.connect(lambda s: self.player.setPlaybackRate(float(s[:-1])))
        row.addWidget(speed)
        row.addStretch(1)
        row.addWidget(self.info)
        top = QHBoxLayout()
        top.addWidget(self.tc)
        top.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addLayout(top)
        lay.addWidget(self.bar)
        lay.addLayout(row)
        self.resize(420, 860)

        load(self.player, clip)
        self.fps = frame_rate(self.player)
        self.player.pause()                       # show the first frame

    def fit(self):
        size = self.video.nativeSize()
        if size.isEmpty():
            return
        self.video.setSize(size)
        self.scene.setSceneRect(QRectF(0, 0, size.width(), size.height()))
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.text.setFont(QFont("Segoe UI", max(12, int(size.width() / 20)), QFont.Weight.Bold))
        self.show_cue(self.cur, force=True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()

    def on_frame(self, frame):
        if frame.startTime() < 0:
            return
        self.t = frame.startTime() / 1e6
        i = cue_at(self.cues, self.t)
        if self.loop_cue and self.cur >= 0 and i != self.cur and self.is_playing():
            self.player.setPosition(int(self.cues[self.cur][0] * 1000) + 1)
            return
        self.show_cue(i)
        self.tc.setText(timecode(self.t, self.fps))
        if not self.bar.isSliderDown():
            self.bar.setValue(int(self.t * 1000))

    def show_cue(self, i, force=False):
        if i == self.cur and not force:
            return
        self.cur = i
        text = self.cues[i][2] if i >= 0 else ""
        self.text.setText(text)
        self.box.setVisible(bool(text))
        self.info.setText(f"#{i + 1}" if i >= 0 else "")
        r = self.scene.sceneRect()
        tb = self.text.boundingRect()
        x, y = (r.width() - tb.width()) / 2, r.height() * 0.72
        self.text.setPos(x, y)
        self.box.setRect(x - 12, y - 6, tb.width() + 24, tb.height() + 12)

    def is_playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def toggle(self):
        playing = not self.is_playing()
        self.player.play() if playing else self.player.pause()
        SP = QStyle.StandardPixmap
        self.b_play.setIcon(self.style().standardIcon(SP.SP_MediaPause if playing else SP.SP_MediaPlay))

    def toggle_loop(self):
        self.loop_cue = not self.loop_cue
        self.b_loop.setChecked(self.loop_cue)

    def step(self, n):
        self.player.pause()
        frame = int(round(self.t * self.fps)) + n
        self.player.setPosition(max(0, int((frame + 0.5) / self.fps * 1000)))   # middle of the frame

    def jump(self, d):
        i = self.cur if self.cur >= 0 else cue_at(self.cues, self.t)
        j = max(0, min(len(self.cues) - 1, (i if i >= 0 else 0) + d))
        self.player.setPosition(int(self.cues[j][0] * 1000) + 1)


# --------------------------------------------------------------------------- the measurements
def selftest(clip, cues, play_seconds):
    player = QMediaPlayer()
    audio = QAudioOutput()
    audio.setVolume(0.0)                          # muted, but the sound device still runs the clock
    player.setAudioOutput(audio)
    sink = QVideoSink()
    player.setVideoOutput(sink)
    t0 = time.perf_counter()
    if not load(player, clip):
        print("could not open the clip:", player.errorString())
        return 1
    fps = frame_rate(player)
    print(f"opened in {time.perf_counter() - t0:.2f} s; frame rate {fps:.3f}; length {player.duration() / 1000:.2f} s")
    player.pause()
    wait_for(sink.videoFrameChanged, 3000)

    def frame_after_seek(ms):
        player.setPosition(ms)
        got = wait_for(sink.videoFrameChanged, 3000, lambda f: f.startTime() >= 0)
        return got[0].startTime() / 1e6 if got else None

    # 1. jumping to each subtitle's start shows the frame that subtitle starts on
    errs, slow = [], []
    for s, _e, _txt in cues:
        a = time.perf_counter()
        ft = frame_after_seek(int(round(s * 1000)) + 1)
        slow.append(time.perf_counter() - a)
        if ft is not None:
            errs.append(int(round(ft * fps)) - int(round(s * fps)))
    print(f"seek to {len(errs)} subtitle starts: frame off by "
          f"{dict(sorted({e: errs.count(e) for e in set(errs)}.items()))}; "
          f"median seek {1000 * statistics.median(slow):.0f} ms, slowest {1000 * max(slow):.0f} ms")

    # 2. stepping one frame at a time lands on every frame, none skipped or repeated
    start = int(round((cues[len(cues) // 2][0] if cues else 1.0) * fps))
    seen = []
    for k in range(60):
        ft = frame_after_seek(int((start + k + 0.5) / fps * 1000))
        if ft is not None:
            seen.append(int(round(ft * fps)) - start)
    bad = [k for k, f in enumerate(seen) if f != k]
    print(f"frame stepping: {len(seen)} steps, {len(bad)} landed on the wrong frame")

    # 3. playing: are frames on time against the wall clock, and are any dropped?
    player.setPosition(0)
    wait_for(sink.videoFrameChanged, 3000)
    log = []
    sink.videoFrameChanged.connect(lambda f: log.append((time.perf_counter(), f.startTime() / 1e6,
                                                         player.position() / 1000))
                                   if f.startTime() >= 0 else None)
    player.play()
    end = time.perf_counter() + play_seconds
    while time.perf_counter() < end and player.mediaStatus() != QMediaPlayer.MediaStatus.EndOfMedia:
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
    player.pause()
    if len(log) > 10:
        w0, f0, _p0 = log[5]
        lag = [(ft - f0) - (w - w0) for w, ft, _p in log[5:]]
        n_frames = int(round((log[-1][1] - log[5][1]) * fps)) + 1
        print(f"played {log[-1][1] - f0:.1f} s: {len(log) - 5} frames shown of {n_frames} "
              f"({n_frames - (len(log) - 5)} dropped); picture vs wall clock: start {1000 * lag[0]:+.0f} ms, "
              f"end {1000 * lag[-1]:+.0f} ms, worst {1000 * max(lag, key=abs):+.0f} ms "
              f"(= {max(abs(x) for x in lag) * fps:.1f} frames)")
        # the player's position follows the sound device; the frame on screen should match it all the way
        sync = [ft - p for _w, ft, p in log[5:]]
        third = max(1, len(sync) // 3)
        print(f"picture vs sound clock: median {1000 * statistics.median(sync):+.0f} ms over the first third, "
              f"{1000 * statistics.median(sync[-third:]):+.0f} ms over the last third, "
              f"worst {1000 * max(sync, key=abs):+.0f} ms")
    else:
        print("played: too few frames arrived to measure")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("clip")
    ap.add_argument("srt")
    ap.add_argument("--selftest", action="store_true", help="measure sync and smoothness, print numbers, quit")
    ap.add_argument("--play-seconds", type=float, default=30.0, help="how long --selftest plays (default 30)")
    a = ap.parse_args()
    app = QApplication(sys.argv)
    cues = read_srt(a.srt)
    if a.selftest:
        return selftest(a.clip, cues, a.play_seconds)
    w = Player(a.clip, cues)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
