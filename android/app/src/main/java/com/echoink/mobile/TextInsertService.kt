package com.echoink.mobile

import android.accessibilityservice.AccessibilityService
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/** Types dictated text at the cursor of whichever text field has focus, in any app. */
class TextInsertService : AccessibilityService() {
    override fun onServiceConnected() {
        instance = this
    }

    override fun onUnbind(intent: Intent?): Boolean {
        instance = null
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    private fun focusedField(): AccessibilityNodeInfo? {
        rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.let {
            if (it.isEditable) return it
        }
        for (window in windows) {
            window.root?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.let {
                if (it.isEditable) return it
            }
        }
        return null
    }

    private fun insertText(text: String): Boolean {
        val field = focusedField() ?: return false
        val current = if (field.isShowingHintText) "" else field.text?.toString() ?: ""
        val start = field.textSelectionStart.let { if (it in 0..current.length) it else current.length }
        val end = field.textSelectionEnd.let { if (it in start..current.length) it else start }
        // Keep a space between the new text and the word before it, as typing would.
        val insertion = if (start > 0 && !current[start - 1].isWhitespace()) " $text" else text

        // Pasting works in almost every app, including web pages, and keeps undo.
        getSystemService(ClipboardManager::class.java)
            .setPrimaryClip(ClipData.newPlainText("EchoInk", insertion))
        if (field.performAction(AccessibilityNodeInfo.ACTION_PASTE)) return true

        val updated = current.substring(0, start) + insertion + current.substring(end)
        val setText = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, updated)
        }
        if (!field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, setText)) return false
        val cursor = start + insertion.length
        field.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, Bundle().apply {
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, cursor)
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, cursor)
        })
        return true
    }

    companion object {
        @Volatile
        private var instance: TextInsertService? = null

        val isEnabled: Boolean
            get() = instance != null

        /** Inserts text at the cursor of the focused field; false if nothing can take it. */
        fun insert(text: String): Boolean = instance?.insertText(text) ?: false
    }
}
