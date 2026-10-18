"""Small floating widgets used on top of the reader."""
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QToolButton, QWidget

from . import theme
from .theme import themed_icon


class OverlayBar(QFrame):
    """A floating frame that fades in and out."""

    def __init__(self, name: str, parent: QWidget):
        super().__init__(parent)
        self.setObjectName(name)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._finished)
        self._shown = False
        self.hide()

    def set_glass(self, backdrop: Callable[[], QPixmap | None], radius: float = 18):
        """Frosted glass: paint a blurred copy of what's behind (from `backdrop`), then a
        see-through tint on top of it."""
        self._backdrop = backdrop
        self._radius = radius

    def paintEvent(self, event):
        backdrop = getattr(self, "_backdrop", None)
        if backdrop is None:
            return super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self._radius, self._radius)
        p.setClipPath(shape)
        blur = backdrop()
        if blur is not None:
            p.drawPixmap(self.rect(), blur)  # stretched up smoothly = blurred
        p.fillPath(shape, QColor(theme.PANEL_TINT))
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.PANEL_EDGE), 1))
        p.drawPath(shape)
        p.end()

    @property
    def shown(self) -> bool:
        return self._shown

    def reveal(self):
        if self._shown:
            return
        self._shown = True
        self._effect.setEnabled(True)
        self.show()
        self.raise_()
        self._animate(1.0)

    def conceal(self):
        if not self._shown:
            return
        self._shown = False
        self._effect.setEnabled(True)
        self._animate(0.0)

    def _animate(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(target)
        self._anim.start()

    def _finished(self):
        # the fade effect re-renders the widget on every frame; switch it off once shown
        if self._shown:
            self._effect.setEnabled(False)
        else:
            self.hide()


def bar_button(glyph: str, tip: str, text: str = "") -> QToolButton:
    b = QToolButton()
    b.setObjectName("BarButton")
    themed_icon(b, glyph)
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tip)
    if text:
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b
