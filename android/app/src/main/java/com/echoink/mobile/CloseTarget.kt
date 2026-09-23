package com.echoink.mobile

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.view.View

/** The ✕ shown at the bottom of the screen while the bubble is dragged; drop it there to close. */
class CloseTarget(context: Context) : View(context) {
    /** True while the finger is over the target; the target grows and turns red. */
    var near = false
        set(value) {
            if (field != value) {
                field = value
                invalidate()
            }
        }

    private val circle = Paint(Paint.ANTI_ALIAS_FLAG)
    private val cross = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFFFFFFF.toInt()
        strokeCap = Paint.Cap.ROUND
    }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val radius = minOf(width, height) / 2f * (if (near) 0.95f else 0.75f)
        circle.color = if (near) 0xEEEF4444.toInt() else 0xCC333333.toInt()
        canvas.drawCircle(cx, cy, radius, circle)
        val arm = radius * 0.35f
        cross.strokeWidth = radius * 0.12f
        canvas.drawLine(cx - arm, cy - arm, cx + arm, cy + arm, cross)
        canvas.drawLine(cx - arm, cy + arm, cx + arm, cy - arm, cross)
    }
}
