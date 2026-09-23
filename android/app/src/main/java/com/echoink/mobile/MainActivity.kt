package com.echoink.mobile

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import java.util.concurrent.Executors
import android.provider.Settings as SystemSettings

/** Pairing, permissions and the on/off switch; the floating mic runs in FloatingMicService. */
class MainActivity : Activity() {
    private lateinit var settings: Settings
    private lateinit var serverField: EditText
    private lateinit var tokenField: EditText
    private lateinit var status: TextView
    private lateinit var permissions: LinearLayout
    private lateinit var toggle: Button
    private val background = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = Settings(this)
        setContentView(buildUi())
        handlePairingLink(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handlePairingLink(intent)
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    override fun onRequestPermissionsResult(requestCode: Int, perms: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(requestCode, perms, results)
        refresh()
    }

    private fun buildUi(): View {
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = dp(20)
            setPadding(pad, pad, pad, pad)
        }
        column.addView(TextView(this).apply {
            text = getString(R.string.app_name)
            textSize = 30f
            typeface = Typeface.DEFAULT_BOLD
        })
        column.addView(text("Dictate into any app with the floating mic. Your PC does the transcription."))

        column.addView(heading("1. Pair with your PC"))
        column.addView(text("On the PC: EchoInk tray icon, Phone access, then scan the code."))
        column.addView(button("Scan pairing QR") { scanPairingCode() })
        serverField = EditText(this).apply {
            hint = "http://100.x.x.x:8765"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            isSingleLine = true
            setText(settings.serverUrl)
        }
        tokenField = EditText(this).apply {
            hint = "Pairing token"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            isSingleLine = true
            setText(settings.token)
        }
        column.addView(serverField)
        column.addView(tokenField)
        column.addView(button("Save and test connection") {
            settings.serverUrl = serverField.text.toString()
            settings.token = tokenField.text.toString()
            testConnection()
        })
        status = text(if (settings.isPaired) "Paired with ${settings.serverUrl}" else "Not paired yet")
        column.addView(status)

        column.addView(heading("2. Permissions"))
        permissions = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        column.addView(permissions)

        column.addView(heading("3. Language"))
        column.addView(languageChoice())

        toggle = button("") { toggleFloatingMic() }
        column.addView(toggle, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(24) })
        column.addView(text("Tap the mic to start, tap again to insert the text. Drag it to move it."))

        return ScrollView(this).apply {
            fitsSystemWindows = true // Android 15 draws apps edge to edge
            addView(column)
        }
    }

    private fun refresh() {
        permissions.removeAllViews()
        permissionRow("Microphone", hasMicrophone()) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
            permissionRow("Notifications (shows when the mic is on)", granted) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2)
            }
        }
        permissionRow("Display over other apps (the floating mic)", SystemSettings.canDrawOverlays(this)) {
            startActivity(Intent(SystemSettings.ACTION_MANAGE_OVERLAY_PERMISSION, packageUri()))
        }
        permissionRow("Accessibility: EchoInk dictation (types the text)", TextInsertService.isEnabled) {
            explainAccessibility()
        }
        toggle.text = if (FloatingMicService.running) "Stop floating mic" else "Start floating mic"
    }

    private fun permissionRow(label: String, granted: Boolean, request: () -> Unit) {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        row.addView(
            text((if (granted) "✓  " else "✗  ") + label),
            LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f)
        )
        if (!granted) row.addView(button("Allow") { request() })
        permissions.addView(row)
    }

    /** Sideloaded apps need "Allow restricted settings" (Android 13+) before accessibility can be turned on. */
    private fun explainAccessibility() {
        AlertDialog.Builder(this)
            .setTitle("Turn on EchoInk dictation")
            .setMessage(
                "In Accessibility, open Installed apps (or Downloaded apps), choose EchoInk dictation " +
                    "and turn it on.\n\nIf it is greyed out: open App info, tap the ⋮ menu at the " +
                    "top right, choose Allow restricted settings, then try again."
            )
            .setPositiveButton("Open Accessibility") { _, _ ->
                startActivity(Intent(SystemSettings.ACTION_ACCESSIBILITY_SETTINGS))
            }
            .setNeutralButton("Open App info") { _, _ ->
                startActivity(Intent(SystemSettings.ACTION_APPLICATION_DETAILS_SETTINGS, packageUri()))
            }
            .show()
    }

    private fun languageChoice(): RadioGroup {
        val group = RadioGroup(this)
        for ((code, label) in listOf("auto" to "Auto: English + Bangla", "en" to "English", "bn" to "Bangla")) {
            group.addView(RadioButton(this).apply {
                id = View.generateViewId()
                text = label
                tag = code
                isChecked = settings.language == code
            })
        }
        group.setOnCheckedChangeListener { g, id -> settings.language = g.findViewById<View>(id).tag as String }
        return group
    }

    private fun toggleFloatingMic() {
        val service = Intent(this, FloatingMicService::class.java)
        when {
            FloatingMicService.running -> stopService(service)
            !settings.isPaired -> toast("Pair with your PC first")
            !hasMicrophone() -> toast("Allow the microphone first")
            !SystemSettings.canDrawOverlays(this) -> toast("Allow display over other apps first")
            else -> startForegroundService(service)
        }
        toggle.postDelayed({ refresh() }, 400)
    }

    private fun scanPairingCode() {
        val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
        GmsBarcodeScanning.getClient(this, options).startScan()
            .addOnSuccessListener { code ->
                if (settings.applyPairingLink(code.rawValue ?: "")) onPaired()
                else toast("That is not an EchoInk pairing code")
            }
            .addOnFailureListener { toast("Scanner unavailable: ${it.message}") }
    }

    private fun handlePairingLink(intent: Intent?) {
        val link = intent?.data?.toString() ?: return
        if (settings.applyPairingLink(link)) onPaired()
    }

    private fun onPaired() {
        serverField.setText(settings.serverUrl)
        tokenField.setText(settings.token)
        testConnection()
    }

    private fun testConnection() {
        status.text = "Checking ${settings.serverUrl} ..."
        background.execute {
            val result = runCatching { EchoInkApi.check(settings) }
            runOnUiThread {
                status.text = result.fold(
                    { "Connected to your PC at ${settings.serverUrl}" },
                    { it.message ?: "Connection failed" }
                )
            }
        }
    }

    private fun hasMicrophone() =
        checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun packageUri() = Uri.parse("package:$packageName")

    private fun heading(label: String) = TextView(this).apply {
        text = label
        textSize = 18f
        typeface = Typeface.DEFAULT_BOLD
        setPadding(0, dp(24), 0, dp(8))
    }

    private fun text(label: String) = TextView(this).apply {
        text = label
        textSize = 15f
        setPadding(0, dp(4), 0, dp(4))
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        setOnClickListener { onClick() }
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
