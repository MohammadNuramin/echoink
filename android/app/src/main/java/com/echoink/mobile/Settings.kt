package com.echoink.mobile

import android.content.Context
import android.net.Uri

/** Where the EchoInk desktop app is, how to talk to it, and where the bubble sits. */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("echoink", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString("server_url", "") ?: ""
        set(value) = prefs.edit().putString("server_url", value.trim().trimEnd('/')).apply()

    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(value) = prefs.edit().putString("token", value.trim()).apply()

    /** "auto" (English or Bangla, decided on the PC), "en" or "bn". */
    var language: String
        get() = prefs.getString("language", "auto") ?: "auto"
        set(value) = prefs.edit().putString("language", value).apply()

    var bubbleX: Int
        get() = prefs.getInt("bubble_x", -1)
        set(value) = prefs.edit().putInt("bubble_x", value).apply()

    var bubbleY: Int
        get() = prefs.getInt("bubble_y", -1)
        set(value) = prefs.edit().putInt("bubble_y", value).apply()

    val isPaired: Boolean
        get() = serverUrl.isNotEmpty() && token.isNotEmpty()

    /** Applies echoink://pair?url=...&token=... from the desktop app's QR code. */
    fun applyPairingLink(link: String): Boolean {
        val uri = Uri.parse(link)
        if (uri.scheme != "echoink" || uri.host != "pair") return false
        val url = uri.getQueryParameter("url") ?: return false
        val token = uri.getQueryParameter("token") ?: return false
        serverUrl = url
        this.token = token
        return true
    }
}
