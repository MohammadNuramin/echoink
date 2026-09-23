package com.echoink.mobile

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Toast
import android.provider.Settings as SystemSettings

/**
 * The home-screen icon. Once EchoInk is set up it just shows the floating mic, like
 * opening an app; otherwise, or if the mic is already showing, it opens the setup screen.
 */
class LaunchActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val ready = Settings(this).isPaired &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED &&
            SystemSettings.canDrawOverlays(this)
        if (ready && !FloatingMicService.running) {
            startForegroundService(Intent(this, FloatingMicService::class.java))
            Toast.makeText(this, "EchoInk is on. Drag the mic onto the X to close it.", Toast.LENGTH_SHORT).show()
        } else {
            startActivity(Intent(this, MainActivity::class.java))
        }
        finish()
    }
}
