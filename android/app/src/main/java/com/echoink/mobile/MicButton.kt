package com.echoink.mobile

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.view.View
import android.view.animation.LinearInterpolator
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.sin

/**
 * The floating mic, drawn like the desktop app's button: a flat black circle with a white mic,
 * white sound bars while recording and a spinner while the PC transcribes. It turns grey while
 * the PC is unreachable and shows how many recordings are waiting to be sent.
 */
class MicButton(context: Context) : View(context) {
    enum class State { IDLE, RECORDING, SENDING }

    var state = State.IDLE
        set(value) {
            field = value
            if (value == State.IDLE) animator.cancel() else if (!animator.isStarted) animator.start()
            invalidate()
        }

    /** True while the PC can't be reached. */
    var offline = false
        set(value) {
            if (field != value) {
                field = value
                invalidate()
            }
        }

    /** Recordings saved on the phone while the PC was unreachable. */
    var pendingCount = 0
        set(value) {
            if (field != value) {
                field = value
                invalidate()
            }
        }

    private val circle = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rim = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = 0x66FFFFFF // keeps the black circle visible on dark screens
    }
    private val white = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFFFFFFF.toInt() }
    private val spinner = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFFFFFFF.toInt()
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val badge = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFF97316.toInt() }
    private val badgeText = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFFFFFFF.toInt()
        textAlign = Paint.Align.CENTER
        isFakeBoldText = true
    }
    private val mic = context.getDrawable(R.drawable.ic_mic_line)!!.mutate()
    private val arc = RectF()
    private var phase = 0f
    private val animator = ValueAnimator.ofFloat(0f, (2 * PI).toFloat()).apply {
        duration = 1_200
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener {
            phase = it.animatedValue as Float
            invalidate()
        }
    }

    init {
        contentDescription = "EchoInk dictation"
    }

    // FloatingMicService's drag listener calls this for taps that don't move.
    override fun performClick(): Boolean = super.performClick()

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val radius = minOf(width, height) / 2f * 0.72f
        val unit = radius / 26f // the desktop button is drawn on a 26-unit radius
        circle.color = if (offline) 0xFF6B7280.toInt() else 0xFF000000.toInt()
        canvas.drawCircle(cx, cy, radius, circle)
        rim.strokeWidth = radius * 0.05f
        canvas.drawCircle(cx, cy, radius, rim)

        when (state) {
            State.IDLE -> {
                val half = (14 * unit).toInt()
                mic.setBounds(cx.toInt() - half, cy.toInt() - half, cx.toInt() + half, cy.toInt() + half)
                mic.draw(canvas)
            }
            State.RECORDING -> {
                val barWidth = 3 * unit
                val gap = 5 * unit
                val start = cx - (5 * barWidth + 4 * gap) / 2
                for (i in 0 until 5) {
                    val barHeight = (6 + 14 * abs(sin(phase + i * 0.9f))) * unit
                    val left = start + i * (barWidth + gap)
                    canvas.drawRoundRect(
                        left, cy - barHeight / 2, left + barWidth, cy + barHeight / 2,
                        barWidth / 2, barWidth / 2, white
                    )
                }
            }
            State.SENDING -> {
                spinner.strokeWidth = 2.5f * unit
                val size = 9 * unit
                arc.set(cx - size, cy - size, cx + size, cy + size)
                canvas.drawArc(arc, Math.toDegrees(phase.toDouble()).toFloat(), 270f, false, spinner)
            }
        }

        if (pendingCount > 0) {
            val badgeRadius = radius * 0.36f
            val bx = cx + radius * 0.72f
            val by = cy - radius * 0.72f
            canvas.drawCircle(bx, by, badgeRadius, badge)
            badgeText.textSize = badgeRadius * 1.3f
            val label = if (pendingCount > 9) "9+" else pendingCount.toString()
            canvas.drawText(label, bx, by + badgeText.textSize * 0.35f, badgeText)
        }
    }

    override fun onDetachedFromWindow() {
        animator.cancel()
        super.onDetachedFromWindow()
    }
}
