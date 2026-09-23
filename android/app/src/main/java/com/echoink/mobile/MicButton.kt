package com.echoink.mobile

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.view.View

/** The floating round mic: orange when idle, pulsing red while recording, blue while transcribing. */
class MicButton(context: Context) : View(context) {
    enum class State { IDLE, RECORDING, SENDING }

    var state = State.IDLE
        set(value) {
            field = value
            if (value == State.IDLE) {
                pulse.cancel()
                pulseFraction = 0f
            } else if (!pulse.isStarted) {
                pulse.start()
            }
            invalidate()
        }

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val icon = context.getDrawable(R.drawable.ic_mic)!!.mutate()
    private var pulseFraction = 0f
    private val pulse = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 900
        repeatCount = ValueAnimator.INFINITE
        addUpdateListener {
            pulseFraction = it.animatedValue as Float
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
        val radius = minOf(width, height) / 2f * 0.7f
        fill.color = when (state) {
            State.IDLE -> 0xFFF97316.toInt()
            State.RECORDING -> 0xFFEF4444.toInt()
            State.SENDING -> 0xFF3B82F6.toInt()
        }
        if (state != State.IDLE) {
            ring.color = fill.color
            ring.alpha = ((1 - pulseFraction) * 200).toInt()
            ring.strokeWidth = radius * 0.12f
            canvas.drawCircle(cx, cy, radius * (1 + 0.38f * pulseFraction), ring)
        }
        canvas.drawCircle(cx, cy, radius, fill)
        val half = radius * 0.55f
        icon.setBounds((cx - half).toInt(), (cy - half).toInt(), (cx + half).toInt(), (cy + half).toInt())
        icon.draw(canvas)
    }

    override fun onDetachedFromWindow() {
        pulse.cancel()
        super.onDetachedFromWindow()
    }
}
