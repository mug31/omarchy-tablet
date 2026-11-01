import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Wacom tablet settings. Every control sends a patch to
// bin/tablet-ctl, which rewrites ~/.config/hypr/tablet.lua and reloads
// Hyprland, so changes take effect immediately and survive a reboot.
// Button mappings are picked up by the tablet-buttons user service
// (bin/tablet-buttons), which watches the same settings file.
Panel {
  id: root
  moduleName: "io.github.mug31.wacom-bamboo"
  ipcTarget: "tablet"

  property var settings_: ({})
  property var monitors: []
  property var area: ({ w: 147.2, h: 92, x: 0, y: 0 })
  property var tabletMm: ({ w: 147.2, h: 92 })
  property bool penPresent: false
  property bool fingerPresent: false
  property bool buttonsService: true
  property bool setupDone: true
  property bool inputAccess: true
  property bool wacomDriver: true
  property string error: ""
  property var pendingPatch: null
  property string tab: "pen"
  property bool editing: false
  property string flashButton: ""
  // What bin/tablet-ctl found: model, capabilities, pen buttons and pad key
  // slots (layout geometry + evdev code, ordered as the tablet is held).
  property var tablet: null

  readonly property color fg: root.bar ? root.bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(fg, 1.4)
  readonly property string uiFont: root.bar ? root.bar.fontFamily : Style.font.family
  readonly property bool connected: tablet !== null
  readonly property bool hasPen: tablet ? tablet.hasPen : true
  readonly property bool hasTouch: tablet ? tablet.hasTouch : false
  readonly property var penButtons: tablet ? tablet.penButtons : []
  readonly property var slots: tablet ? tablet.slots : []
  readonly property int rotation_: s("rotation", 0)
  readonly property string modelTitle: tablet ? String(tablet.modelName).replace(/^Wacom /, "") : "Tablet"

  function tabOptions() {
    var opts = []
    if (hasPen) opts.push({ value: "pen", label: "Pen", icon: "\uf040" })
    if (hasTouch) opts.push({ value: "touch", label: "Touch", icon: "\uf25a" })
    if (penButtons.length > 0 || slots.length > 0) opts.push({ value: "buttons", label: "Buttons", icon: "\uf11c" })
    return opts
  }

  // Keep the selected tab valid when a tablet with fewer features is plugged in.
  onTabletChanged: {
    var opts = tabOptions()
    for (var i = 0; i < opts.length; i++) if (opts[i].value === tab) return
    if (opts.length > 0) tab = opts[0].value
  }

  readonly property var penButtonInfo: ({
    pen1: { label: "Lower button", hint: "nearest the tip" },
    pen2: { label: "Upper button", hint: "further from the tip" },
    pen3: { label: "Third button", hint: "furthest from the tip" }
  })
  readonly property string home: Quickshell.env("HOME")

  readonly property string pluginDir: {
    var url = String(Qt.resolvedUrl("."))
    if (url.indexOf("file://") === 0) url = url.substring(7)
    return url.replace(/\/$/, "")
  }
  readonly property string helper: pluginDir + "/bin/tablet-ctl"

  readonly property var penActions: [
    { value: "default", label: "App default" },
    { value: "left", label: "Left click" },
    { value: "right", label: "Right click" },
    { value: "middle", label: "Middle click" },
    { value: "key", label: "Shortcut" },
    { value: "command", label: "Command" },
    { value: "none", label: "Disabled" }
  ]
  readonly property var padActions: penActions

  // Ready-made values for the Shortcut and Command actions.
  readonly property var keyPresets: [
    { value: "", label: "Presets" },
    { value: "ctrl+z", label: "Undo" },
    { value: "ctrl+shift+z", label: "Redo" },
    { value: "space", label: "Pan (hold)" },
    { value: "e", label: "Eraser" },
    { value: "b", label: "Brush" },
    { value: "[", label: "Smaller brush" },
    { value: "]", label: "Bigger brush" },
    { value: "ctrl+plus", label: "Zoom in" },
    { value: "ctrl+minus", label: "Zoom out" },
    { value: "ctrl+0", label: "Reset zoom" },
    { value: "ctrl+c", label: "Copy" },
    { value: "ctrl+v", label: "Paste" },
    { value: "ctrl+s", label: "Save" },
    { value: "esc", label: "Escape" }
  ]
  readonly property var commandPresets: [
    { value: "", label: "Presets" },
    { value: root.helper + " toggle fingerEnabled", label: "Toggle finger touch" },
    { value: "omarchy capture screenshot region", label: "Screenshot region" },
    { value: "hyprpicker -a", label: "Pick a colour" },
    { value: "omarchy-shell tablet open", label: "Open this panel" }
  ]

  function s(key, fallback) {
    var v = settings_[key]
    return v === undefined ? fallback : v
  }

  function buttonSpec(key) {
    var b = settings_.buttons || {}
    return b[key] || { action: "none", value: "" }
  }

  function monitorOptions() {
    var opts = [{ value: "", label: "All" }]
    for (var i = 0; i < monitors.length; i++) {
      var m = monitors[i]
      var brand = String(m.description || "").split(" ")[0]
      opts.push({ value: m.name, label: brand || m.name, tooltip: m.name + " · " + m.description })
    }
    return opts
  }

  function applyStatus(raw) {
    var data
    try { data = JSON.parse(raw) } catch (e) { return }
    if (!data) return
    // Keep optimistic values while a newer patch is still queued.
    if (!pendingPatch) settings_ = data.settings || {}
    monitors = data.monitors || []
    area = data.area || area
    tabletMm = data.tabletMm || tabletMm
    penPresent = !!data.pen
    fingerPresent = !!data.finger
    buttonsService = data.buttonsService !== false
    setupDone = data.setupDone !== false
    inputAccess = data.inputAccess !== false
    wacomDriver = data.wacomDriver !== false
    error = data.error || ""
    tablet = data.tablet || null
  }

  function refresh() {
    if (setProc.running || statusProc.running) return
    statusProc.running = true
  }

  // `local` merges into the panel's copy of the settings; slotCodes is only
  // meaningful to the helper, which stores it per tablet model.
  function mergePatch(base, patch, local) {
    var out = Object.assign({}, base || {})
    for (var k in patch) {
      if (k === "slotCodes" && local) {
        continue
      } else if (k === "buttons") {
        var b = Object.assign({}, out.buttons || {})
        for (var bk in patch.buttons) b[bk] = Object.assign({}, b[bk] || {}, patch.buttons[bk])
        out.buttons = b
      } else {
        out[k] = patch[k]
      }
    }
    return out
  }

  // Optimistic update, then hand the patch to the helper. Patches that
  // arrive while one is being written are merged and sent as one.
  function send(patch) {
    settings_ = mergePatch(settings_, patch, true)
    pendingPatch = mergePatch(pendingPatch, patch, false)
    if (!setProc.running) flush()
  }

  function set(key, value) {
    var patch = {}
    patch[key] = value
    send(patch)
  }

  function setButton(key, spec) {
    var buttons = {}
    buttons[key] = spec
    send({ buttons: buttons })
  }

  function flush() {
    if (!pendingPatch) return
    setProc.command = [root.helper, "set", JSON.stringify(pendingPatch)]
    pendingPatch = null
    setProc.running = true
  }

  onOpenedChanged: {
    if (opened) {
      refresh()
    } else {
      editing = false
      if (identifying) stopIdentify()
    }
  }
  Component.onCompleted: refresh()

  Process {
    id: statusProc
    command: [root.helper, "status"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyStatus(text) }
  }

  Process {
    id: setProc
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyStatus(text) }
    onExited: root.flush()
  }

  Process {
    id: setupProc
    command: [root.helper, "setup"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyStatus(text) }
  }

  Process {
    id: resetProc
    command: [root.helper, "reset"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyStatus(text) }
  }

  // tablet-buttons writes "<button> <time>" on every press; light up its row.
  // Polled only while the Buttons tab is showing.
  property string lastPress: ""

  function onPressLine(raw) {
    var line = String(raw || "").trim()
    if (line === "" || line === lastPress) return
    var first = lastPress === ""
    lastPress = line
    if (first) return
    var name = line.split(" ")[0]
    flashButton = name
    flashTimer.restart()
    if (identifying && slotIndexOf(name) >= 0 && identifyOrder.indexOf(name) < 0) {
      var next = identifyOrder.concat([name])
      identifyOrder = next
      if (next.length === slots.length) {
        // The i-th key pressed sits at the i-th position as the tablet is held.
        var map = {}
        for (var i = 0; i < slots.length; i++) map[slots[i].letter] = next[i]
        set("slotCodes", map)
        stopIdentify()
        refresh()
      }
    }
  }

  // ---- "Identify keys": press the pad keys in order to learn which is which.
  property bool identifying: false
  property var identifyOrder: []

  function slotIndexOf(code) {
    for (var i = 0; i < slots.length; i++) if (slots[i].code === code) return i
    return -1
  }

  function startIdentify() {
    identifyOrder = []
    identifying = true
    flagProc.command = ["sh", "-c", "mkdir -p \"$HOME/.cache/omarchy-tablet\" && touch \"$HOME/.cache/omarchy-tablet/identify\""]
    flagProc.running = true
  }

  function stopIdentify() {
    identifying = false
    flagProc.running = false
    flagProc.command = ["rm", "-f", root.home + "/.cache/omarchy-tablet/identify"]
    flagProc.running = true
  }

  Process { id: flagProc }
  Process { id: testProc }

  Process {
    id: pressProc
    command: ["cat", root.home + "/.cache/omarchy-tablet/last-button"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.onPressLine(text) }
  }

  Timer {
    interval: 250
    repeat: true
    triggeredOnStart: true
    running: root.opened && root.tab === "buttons"
    onTriggered: if (!pressProc.running) pressProc.running = true
  }

  Timer { id: flashTimer; interval: 900; onTriggered: root.flashButton = "" }

  // Plugging or unplugging the tablet is noticed on the next tick.
  Timer { interval: root.opened ? 3000 : 15000; running: true; repeat: true; onTriggered: root.refresh() }

  // Hidden while no tablet is plugged in, unless setup still needs the panel.
  visible: root.connected || !root.setupDone || !root.wacomDriver
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    opacity: root.connected ? 1 : 0.45
    tooltipText: root.connected ? "Tablet" : "Tablet not connected"
    onPressed: function(b) { root.toggle() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.editing
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      // Scrolls when the panel is taller than the display (Buttons tab).
      Flickable {
        id: scroller
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

      Column {
        id: column
        width: scroller.width
        spacing: Style.space(14)

        PanelHero {
          foreground: root.fg
          fontFamily: root.uiFont
          title: root.modelTitle
          meta: root.connected
            ? ((root.tablet.model ? root.tablet.model + " · " : "") + "connected")
            : "No Wacom tablet connected"
          detail: root.connected
            ? [root.hasPen ? "PEN" : "", root.hasTouch ? "TOUCH" : ""].filter(function(x) { return x }).join(" + ")
            : ""
          iconOpacity: root.connected ? 1 : 0.45
          iconComponent: Text {
            textFormat: Text.PlainText
            text: ""
            color: root.fg
            font.family: root.uiFont
            font.pixelSize: Style.font.display
          }
        }

        // First run / missing pieces.
        Column {
          visible: !root.setupDone || !root.wacomDriver
          width: parent.width
          spacing: Style.space(8)

          Text {
            width: parent.width
            text: !root.wacomDriver
              ? "The kernel wacom driver isn't loaded, so the tablet can't be seen. OpenTabletDriver blocks it; see the plugin README."
              : "One-time setup: adds hypr/tablet.lua to your Hyprland config and starts the tablet-buttons service."
            color: root.fg
            font.family: root.uiFont
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Button {
            visible: !root.setupDone
            anchors.right: parent.right
            text: setupProc.running ? "Setting up…" : "Finish setup"
            iconText: "\uf0ad"
            bordered: true
            foreground: root.fg
            fontFamily: root.uiFont
            fontSize: Style.font.bodySmall
            onClicked: if (!setupProc.running) setupProc.running = true
          }
        }

        Text {
          visible: root.error !== ""
          width: parent.width
          text: root.error
          color: Color.urgent
          font.family: root.uiFont
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }

        ButtonGroup {
          id: tabs
          width: parent.width
          spacing: Style.space(6)
          focusable: false
          visible: root.tabOptions().length > 1
          options: root.tabOptions()
          value: root.tab
          foreground: root.fg
          fontFamily: root.uiFont
          fontSize: Style.font.bodySmall
          onChanged: function(v) { root.tab = v; root.editing = false; scroller.contentY = 0; keyCatcher.forceActiveFocus() }
        }

        PanelSeparator { foreground: root.fg }

        // ================= Pen =================
        Column {
          visible: root.tab === "pen" && root.hasPen
          width: parent.width
          spacing: Style.space(14)

          Header { text: "TARGET DISPLAY" }
          ButtonGroup {
            width: parent.width
            spacing: Style.space(6)
            focusable: false
            options: root.monitorOptions()
            value: String(root.s("output", ""))
            foreground: root.fg
            fontFamily: root.uiFont
            fontSize: Style.font.bodySmall
            onChanged: function(v) { root.set("output", v) }
          }

          Header { text: "ORIENTATION" }
          ButtonGroup {
            width: parent.width
            spacing: Style.space(6)
            focusable: false
            options: [
              { value: "0", label: "Normal" },
              { value: "180", label: "Flipped", tooltip: "Turned 180° for left-handed use" },
              { value: "90", label: "90°", tooltip: "Portrait, turned clockwise" },
              { value: "270", label: "270°", tooltip: "Portrait, turned counter-clockwise" }
            ]
            value: String(root.s("rotation", 0))
            foreground: root.fg
            fontFamily: root.uiFont
            fontSize: Style.font.bodySmall
            onChanged: function(v) { root.set("rotation", parseInt(v)) }
          }

          Text {
            width: parent.width
            text: root.rotation_ === 180
              ? "Flipped for left-handed use: pen" + (root.hasTouch ? ", touch" : "") + " and the key order all turn around."
              : root.rotation_ === 0
                ? "Left-handed? Turn the tablet around and pick Flipped."
                : !root.buttonsService || !root.inputAccess
                  ? "Portrait needs the tablet-buttons service to turn the pen" + (root.hasTouch ? " and touch" : "") + ", and it isn't running."
                  : "Portrait: pen" + (root.hasTouch ? ", touch" : "") + " and the key order all turn with the tablet."
            color: root.dim
            font.family: root.uiFont
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          SliderRow {
            title: "ACTIVE AREA"
            minimum: 30
            maximum: 100
            step: 5
            integer: true
            value: root.s("areaPercent", 100)
            valueText: Math.round(liveValue) + "% · " + root.area.w + "×" + root.area.h + " mm"
            onCommit: function(v) { root.set("areaPercent", Math.round(v)) }
          }

          // Scale drawing of the tablet surface with the active area inside.
          Item {
            width: parent.width
            height: Style.space(84)

            Rectangle {
              id: surface
              readonly property real k: Math.min(parent.width / root.tabletMm.w, parent.height / root.tabletMm.h)
              width: root.tabletMm.w * k
              height: root.tabletMm.h * k
              anchors.centerIn: parent
              color: "transparent"
              radius: Style.cornerRadius
              border.width: 1
              border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.3)

              Rectangle {
                x: root.area.x * surface.k
                y: root.area.y * surface.k
                width: root.area.w * surface.k
                height: root.area.h * surface.k
                radius: Style.cornerRadius
                color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.14)
                border.width: 1
                border.color: root.fg
                Behavior on x { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                Behavior on y { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                Behavior on height { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
              }
            }
          }

          SwitchRow {
            label: "Match display aspect ratio"
            description: "Circles stay round instead of stretched"
            checked: root.s("matchAspect", true) === true
            onToggled: root.set("matchAspect", !checked)
          }

          SliderRow {
            title: "PRESSURE SENSITIVITY"
            minimum: 0
            maximum: 100
            step: 5
            integer: true
            value: root.s("pressure", 0)
            valueText: liveValue <= 0 ? "Normal" : "+" + Math.round(liveValue) + "%"
            onCommit: function(v) { root.set("pressure", Math.round(v)) }
          }
        }

        // ================= Touch =================
        Column {
          visible: root.tab === "touch" && root.hasTouch
          width: parent.width
          spacing: Style.space(14)

          SwitchRow {
            label: "Finger touch"
            description: "Turn off to rest your hand while drawing"
            checked: root.s("fingerEnabled", true) === true
            onToggled: root.set("fingerEnabled", !checked)
          }

          Column {
            width: parent.width
            spacing: Style.space(14)
            enabled: root.s("fingerEnabled", true) === true
            opacity: enabled ? 1 : 0.4
            Behavior on opacity { NumberAnimation { duration: 140 } }

            SliderRow {
              title: "POINTER SPEED"
              minimum: -1
              maximum: 1
              step: 0.1
              value: root.s("fingerSpeed", 0)
              valueText: (liveValue > 0 ? "+" : "") + Number(liveValue).toFixed(1)
              onCommit: function(v) { root.set("fingerSpeed", Math.round(v * 10) / 10) }
            }

            SwitchRow {
              label: "Tap to click"
              checked: root.s("tapToClick", true) === true
              onToggled: root.set("tapToClick", !checked)
            }

            SwitchRow {
              label: "Natural scrolling"
              description: "Two-finger scroll moves the content, like a phone"
              checked: root.s("naturalScroll", true) === true
              onToggled: root.set("naturalScroll", !checked)
            }
          }
        }

        // ================= Buttons =================
        Column {
          visible: root.tab === "buttons"
          width: parent.width
          spacing: Style.space(12)

          Text {
            width: parent.width
            text: !root.inputAccess
              ? "Your user can't read the tablet's input devices, so mappings won't apply. Add yourself to the input group (sudo usermod -aG input $USER), then log out and back in."
              : root.buttonsService
                ? "Press any pen or tablet button: its picture and row light up. App default leaves a button to the app you're in (Krita, Rnote…)."
                : "The tablet-buttons service is not running, so mappings won't apply. Start it with: systemctl --user start tablet-buttons"
            color: root.buttonsService && root.inputAccess ? root.dim : Color.urgent
            font.family: root.uiFont
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Header { visible: root.penButtons.length > 0; text: "PEN SIDE BUTTONS" }
          PenDiagram { visible: root.penButtons.length > 0; width: parent.width }
          Repeater {
            model: root.penButtons
            ButtonRow {
              required property var modelData
              key: modelData
              label: (root.penButtonInfo[modelData] || { label: modelData }).label
              hint: (root.penButtonInfo[modelData] || { hint: "" }).hint
              options: root.penActions
            }
          }

          PanelSeparator { visible: root.penButtons.length > 0 && root.slots.length > 0; foreground: root.fg }

          Item {
            visible: root.slots.length > 0
            width: parent.width
            implicitHeight: identifyButton.implicitHeight

            Header {
              text: "TABLET EXPRESSKEYS"
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
            }

            Button {
              id: identifyButton
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: root.identifying ? "Cancel" : "Identify keys"
              iconText: root.identifying ? "" : ""
              bordered: true
              foreground: root.fg
              fontFamily: root.uiFont
              fontSize: Style.font.caption
              onClicked: root.identifying ? root.stopIdentify() : root.startIdentify()
            }
          }

          TabletDiagram { visible: root.slots.length > 0; width: parent.width }

          Text {
            visible: root.slots.length > 0
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: root.identifying && root.identifyOrder.length < root.slots.length
              ? "Press the " + root.slots[root.identifyOrder.length].label.toUpperCase()
                + " on the tablet  (" + (root.identifyOrder.length + 1) + "/" + root.slots.length + ")\nKey actions are paused until you finish."
              : root.tablet && !root.tablet.orderKnown
                ? "This model's key order is a guess. Press Identify keys and tap each key in the order shown."
                : "Drawn as you hold the tablet" + (root.rotation_ === 180 ? " (flipped)" : "") + ". Wrong order? Use Identify keys."
            color: root.identifying ? root.fg : root.dim
            font.family: root.uiFont
            font.pixelSize: Style.font.caption
            font.bold: root.identifying
            wrapMode: Text.WordWrap
          }

          Repeater {
            model: root.slots
            ButtonRow {
              required property var modelData
              key: modelData.code
              label: modelData.label
              options: root.padActions
            }
          }
        }

        PanelSeparator { foreground: root.fg }

        Button {
          anchors.right: parent.right
          text: "Reset to defaults"
          iconText: ""
          bordered: true
          foreground: root.fg
          fontFamily: root.uiFont
          fontSize: Style.font.bodySmall
          onClicked: if (!resetProc.running) resetProc.running = true
        }
      }
      }
    }
  }

  component Header: PanelSectionHeader {
    foreground: root.fg
    fontFamily: root.uiFont
  }

  component SliderRow: Column {
    id: sliderRow
    property string title: ""
    property string valueText: ""
    property alias value: slider.value
    property alias minimum: slider.minimum
    property alias maximum: slider.maximum
    property alias step: slider.step
    property alias integer: slider.integer
    readonly property real liveValue: slider.dragging ? slider.liveValue : slider.value
    signal commit(real value)

    width: parent.width
    spacing: Style.space(6)

    Item {
      width: parent.width
      implicitHeight: Math.max(sliderTitle.implicitHeight, sliderValue.implicitHeight)

      Header {
        id: sliderTitle
        text: sliderRow.title
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        id: sliderValue
        textFormat: Text.PlainText
        text: sliderRow.valueText
        color: root.dim
        font.family: root.uiFont
        font.pixelSize: Style.font.caption
        font.bold: true
        anchors.right: parent.right
        anchors.rightMargin: Style.space(6)
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    PanelSlider {
      id: slider
      bar: root.bar
      width: parent.width
      onReleased: function(v) { sliderRow.commit(v) }
    }
  }

  component SwitchRow: Item {
    id: switchRow
    property string label: ""
    property string description: ""
    property bool checked: false
    signal toggled()

    width: parent.width
    implicitHeight: Math.max(labels.implicitHeight, sw.implicitHeight)

    Column {
      id: labels
      anchors.left: parent.left
      anchors.right: sw.left
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(2)

      Text {
        textFormat: Text.PlainText
        text: switchRow.label
        width: parent.width
        color: root.fg
        font.family: root.uiFont
        font.pixelSize: Style.font.bodySmall
        elide: Text.ElideRight
      }

      Text {
        textFormat: Text.PlainText
        visible: switchRow.description !== ""
        text: switchRow.description
        width: parent.width
        color: root.dim
        font.family: root.uiFont
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }
    }

    ToggleSwitch {
      id: sw
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      trackHeight: Style.space(18)
      foreground: root.fg
      checked: switchRow.checked
      onToggled: switchRow.toggled()
    }

    MouseArea {
      anchors.left: parent.left
      anchors.right: sw.left
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      cursorShape: Qt.PointingHandCursor
      onClicked: switchRow.toggled()
    }
  }

  // One remappable button: name, action picker, and a text field for the
  // shortcut or command when the action needs one. Flashes on press.
  component ButtonRow: Item {
    id: buttonRow
    property string key: ""
    property string label: ""
    property string hint: ""
    property var options: []
    readonly property var spec: root.buttonSpec(key)
    readonly property bool needsValue: spec.action === "key" || spec.action === "command"
    readonly property bool flashing: root.flashButton === key

    width: parent.width
    implicitHeight: rowContent.implicitHeight + Style.space(8)

    Rectangle {
      anchors.fill: parent
      anchors.margins: -Style.space(4)
      radius: Style.cornerRadius
      color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, buttonRow.flashing ? 0.16 : 0)
      Behavior on color { ColorAnimation { duration: buttonRow.flashing ? 60 : 400 } }
    }

    Column {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(6)

      Item {
        width: parent.width
        implicitHeight: picker.implicitHeight

        Column {
          anchors.left: parent.left
          anchors.right: picker.left
          anchors.rightMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(1)

          Text {
            textFormat: Text.PlainText
            text: buttonRow.label
            width: parent.width
            elide: Text.ElideRight
            color: root.fg
            font.family: root.uiFont
            font.pixelSize: Style.font.bodySmall
            font.bold: buttonRow.flashing
          }

          Text {
            textFormat: Text.PlainText
            visible: buttonRow.hint !== ""
            text: buttonRow.hint
            width: parent.width
            elide: Text.ElideRight
            color: root.dim
            font.family: root.uiFont
            font.pixelSize: Style.font.caption
          }
        }

        Dropdown {
          id: picker
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(170)
          showLabel: false
          options: buttonRow.options
          value: String(buttonRow.spec.action)
          foreground: root.fg
          fontFamily: root.uiFont
          onChanged: function(v) {
            var next = { action: v }
            if (v === "key" && buttonRow.spec.action !== "key") next.value = "ctrl+z"
            if (v === "command" && buttonRow.spec.action !== "command") next.value = ""
            root.setButton(buttonRow.key, next)
          }
        }
      }

      Item {
        visible: buttonRow.needsValue
        width: parent.width
        implicitHeight: Math.max(valueField.implicitHeight, presetPicker.implicitHeight)

      PlainField {
        id: valueField
        anchors.left: parent.left
        anchors.right: presetPicker.left
        anchors.rightMargin: Style.space(6)
        anchors.verticalCenter: parent.verticalCenter
        text: String(buttonRow.spec.value || "")
        placeholderText: buttonRow.spec.action === "key" ? "e.g. ctrl+z, ctrl+shift+s, super+w" : "e.g. omarchy launch browser"
        onEditingFinished: if (text !== String(buttonRow.spec.value || "")) root.setButton(buttonRow.key, { value: text })
      }

      Dropdown {
        id: presetPicker
        anchors.right: testButton.visible ? testButton.left : parent.right
        anchors.rightMargin: testButton.visible ? Style.space(6) : 0
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(110)
        showLabel: false
        options: buttonRow.spec.action === "key" ? root.keyPresets : root.commandPresets
        value: ""
        foreground: root.fg
        fontFamily: root.uiFont
        onChanged: function(v) {
          if (v !== "") root.setButton(buttonRow.key, { value: v })
          Qt.callLater(function() { presetPicker.value = "" })
        }
      }

      // Runs the command now, so it can be checked without reaching for the pen.
      Button {
        id: testButton
        visible: buttonRow.spec.action === "command"
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: "Test"
        iconText: "\uf04b"
        bordered: true
        enabled: String(buttonRow.spec.value || "").trim() !== ""
        foreground: root.fg
        fontFamily: root.uiFont
        fontSize: Style.font.caption
        onClicked: {
          testProc.command = ["sh", "-c", String(buttonRow.spec.value)]
          testProc.running = true
        }
      }
      }
    }
  }

  // One-line text field built on TextEdit rather than TextInput/TextField.
  // Qt's TextInput reads the whole clipboard offer every time the clipboard
  // changes (to update canPaste), with no size limit or timeout, which lets
  // any clipboard owner stall or bloat the shared shell. TextEdit only looks
  // at the offered formats. Pasting goes through wl-paste in its own process
  // instead, capped at 2 s and 1 KB; dropped text is ignored.
  component PlainField: Rectangle {
    id: field
    property alias text: edit.text
    property string placeholderText: ""
    signal editingFinished()

    function paste() {
      if (pasteProc.running) return
      pasteProc.running = true
    }

    implicitHeight: Style.spacing.controlHeight
    radius: Style.cornerRadius
    color: "transparent"
    border.width: 1
    border.color: edit.activeFocus ? Color.accent : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, mouse.containsMouse ? 0.7 : 0.4)

    TextEdit {
      id: edit
      anchors.fill: parent
      anchors.leftMargin: Style.spacing.controlPaddingX
      anchors.rightMargin: Style.spacing.controlPaddingX
      verticalAlignment: TextEdit.AlignVCenter
      textFormat: TextEdit.PlainText
      wrapMode: TextEdit.NoWrap
      clip: true
      selectByMouse: true
      color: root.fg
      selectionColor: Style.selectionFillFor(root.fg, Color.accent)
      selectedTextColor: root.fg
      font.family: root.uiFont
      font.pixelSize: Style.font.bodySmall

      onActiveFocusChanged: {
        root.editing = activeFocus
        if (!activeFocus) field.editingFinished()
      }
      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Escape) {
          keyCatcher.forceActiveFocus()
          event.accepted = true
        } else if (event.matches(StandardKey.Paste)
                   || (event.key === Qt.Key_V && (event.modifiers & Qt.ControlModifier))) {
          field.paste()
          event.accepted = true
        } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
          event.accepted = true
        }
      }
    }

    Text {
      visible: edit.text === "" && !edit.activeFocus
      anchors.left: edit.left
      anchors.right: edit.right
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: field.placeholderText
      elide: Text.ElideRight
      color: root.dim
      font: edit.font
    }

    // Middle-click would paste the primary selection through Qt's reader.
    MouseArea {
      id: mouse
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.MiddleButton
      cursorShape: Qt.IBeamCursor
      onClicked: { edit.forceActiveFocus(); field.paste() }
    }

    DropArea { anchors.fill: parent; onDropped: function(drop) { drop.accepted = true } }

    Process {
      id: pasteProc
      command: ["sh", "-c", "timeout 2 wl-paste --no-newline --type text 2>/dev/null | head -c 1024"]
      stdout: StdioCollector {
        waitForEnd: true
        onStreamFinished: {
          var t = String(text).replace(/[\r\n\t]+/g, " ")
          if (t === "") return
          if (edit.selectedText !== "") edit.remove(edit.selectionStart, edit.selectionEnd)
          edit.insert(edit.cursorPosition, t)
        }
      }
    }
  }

  // Pen seen from the side, tip on the left; side buttons light up on press.
  component PenDiagram: Item {
    id: penDiagram
    implicitHeight: Style.space(54)

    readonly property real bodyW: Math.min(width, Style.space(300))
    readonly property real tipW: Style.space(34)
    readonly property real bodyH: Style.space(14)

    Item {
      id: pen
      width: penDiagram.bodyW
      height: penDiagram.bodyH
      anchors.horizontalCenter: parent.horizontalCenter
      y: Style.space(8)

      Canvas {
        id: tip
        width: penDiagram.tipW
        height: parent.height
        property color c: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.55)
        onCChanged: requestPaint()
        onPaint: {
          var ctx = getContext("2d")
          ctx.reset()
          ctx.fillStyle = c
          ctx.beginPath()
          ctx.moveTo(0, height / 2)
          ctx.lineTo(width, 0)
          ctx.lineTo(width, height)
          ctx.closePath()
          ctx.fill()
        }
      }

      Rectangle {
        id: barrel
        x: tip.width
        width: parent.width - tip.width
        height: parent.height
        radius: height / 2
        color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.12)
        border.width: 1
        border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.55)
      }

      Repeater {
        model: root.penButtons.map(function(k, i) { return { key: k, x: 0.16 + i * 0.24 } })
        Rectangle {
          required property var modelData
          readonly property bool lit: root.flashButton === modelData.key
          x: barrel.x + barrel.width * modelData.x
          y: -height / 2 + 1
          width: Style.space(24)
          height: Style.space(9)
          radius: height / 2
          color: lit ? root.fg : Color.background
          border.width: 1
          border.color: root.fg
          Behavior on color { ColorAnimation { duration: lit ? 60 : 400 } }
        }
      }
    }

    Repeater {
      model: root.penButtons.map(function(k, i) {
        return { key: k, x: 0.16 + i * 0.24, label: ["Lower", "Upper", "Third"][i] || k }
      })
      Text {
        required property var modelData
        textFormat: Text.PlainText
        text: modelData.label
        x: pen.x + barrel.x + barrel.width * modelData.x + Style.space(12) - width / 2
        y: pen.y + pen.height + Style.space(8)
        color: root.flashButton === modelData.key ? root.fg : root.dim
        font.family: root.uiFont
        font.pixelSize: Style.font.caption
        font.bold: root.flashButton === modelData.key
      }
    }

    Text {
      textFormat: Text.PlainText
      text: "tip"
      x: pen.x
      y: pen.y + pen.height + Style.space(8)
      color: root.dim
      font.family: root.uiFont
      font.pixelSize: Style.font.caption
    }
  }

  // Tablet seen from above, drawn from libwacom's layout for the model and
  // turned to match the chosen orientation. Key numbers are positions as the
  // tablet is held (1 = top/leftmost); a key lights up when pressed and the one
  // "Identify keys" is waiting for is outlined in the accent colour.
  component TabletDiagram: Item {
    id: tabletDiagram

    readonly property real cw: root.tablet && root.tablet.canvas ? root.tablet.canvas[0] : 240
    readonly property real ch: root.tablet && root.tablet.canvas ? root.tablet.canvas[1] : 160
    readonly property bool quarter: root.rotation_ === 90 || root.rotation_ === 270
    // Size of the drawing once turned.
    readonly property real shownW: quarter ? ch : cw
    readonly property real shownH: quarter ? cw : ch
    readonly property real k: Math.min(width / shownW, Style.space(150) / shownH)

    implicitHeight: shownH * k + Style.space(4)

    // Bounding box of the keys, to place the drawing area beside them.
    readonly property var keyBox: {
      var sl = root.slots
      if (!sl.length) return { x: 0, y: 0, w: 0, h: 0 }
      var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9
      for (var i = 0; i < sl.length; i++) {
        x0 = Math.min(x0, sl[i].x); y0 = Math.min(y0, sl[i].y)
        x1 = Math.max(x1, sl[i].x + sl[i].w); y1 = Math.max(y1, sl[i].y + sl[i].h)
      }
      return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
    }
    readonly property var drawArea: {
      var b = keyBox, m = 0.06
      if (b.h >= b.w) {  // a column of keys: surface on the far side
        var left = b.x + b.w / 2 < cw / 2
        var x0 = left ? b.x + b.w + cw * m : cw * m
        var x1 = left ? cw * (1 - m) : b.x - cw * m
        return { x: x0, y: ch * 0.1, w: x1 - x0, h: ch * 0.8 }
      }
      var top = b.y + b.h / 2 < ch / 2  // a row of keys
      var y0 = top ? b.y + b.h + ch * m : ch * 0.1
      var y1 = top ? ch * 0.9 : b.y - ch * m
      return { x: cw * 0.08, y: y0, w: cw * 0.84, h: y1 - y0 }
    }

    Item {
      id: drawing
      width: tabletDiagram.cw * tabletDiagram.k
      height: tabletDiagram.ch * tabletDiagram.k
      anchors.centerIn: parent
      rotation: root.rotation_
      Behavior on rotation { RotationAnimation { duration: 260; easing.type: Easing.OutCubic; direction: RotationAnimation.Shortest } }

      Rectangle {
        anchors.fill: parent
        radius: Style.space(12)
        color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.05)
        border.width: 1
        border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.55)
      }

      // Drawing surface.
      Rectangle {
        visible: tabletDiagram.drawArea.w > 0 && tabletDiagram.drawArea.h > 0
        x: tabletDiagram.drawArea.x * tabletDiagram.k
        y: tabletDiagram.drawArea.y * tabletDiagram.k
        width: Math.max(0, tabletDiagram.drawArea.w * tabletDiagram.k)
        height: Math.max(0, tabletDiagram.drawArea.h * tabletDiagram.k)
        radius: Style.space(6)
        color: "transparent"
        border.width: 1
        border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.25)

        Text {
          anchors.centerIn: parent
          rotation: -root.rotation_
          textFormat: Text.PlainText
          text: "drawing area"
          color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.35)
          font.family: root.uiFont
          font.pixelSize: Style.font.caption
        }
      }

      Repeater {
        model: root.slots
        Rectangle {
          required property var modelData
          readonly property string code: root.identifying ? (root.identifyOrder[modelData.index] || "") : modelData.code
          readonly property bool lit: code !== "" && root.flashButton === code
          readonly property bool waiting: root.identifying && modelData.index === root.identifyOrder.length
          readonly property bool done: root.identifying && modelData.index < root.identifyOrder.length

          x: modelData.x * tabletDiagram.k
          y: modelData.y * tabletDiagram.k
          width: Math.max(Style.space(12), modelData.w * tabletDiagram.k)
          height: Math.max(Style.space(12), modelData.h * tabletDiagram.k)
          radius: Style.space(4)
          color: lit ? root.fg : (done ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.3) : Color.background)
          border.width: waiting ? 2 : 1
          border.color: waiting ? Color.accent : root.fg
          Behavior on color { ColorAnimation { duration: lit ? 60 : 400 } }

          SequentialAnimation on opacity {
            running: waiting
            loops: Animation.Infinite
            alwaysRunToEnd: true
            NumberAnimation { to: 0.35; duration: 500; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 500; easing.type: Easing.InOutSine }
          }

          Text {
            anchors.centerIn: parent
            rotation: -root.rotation_
            textFormat: Text.PlainText
            text: String(modelData.index + 1)
            color: parent.lit ? Color.background : root.fg
            font.family: root.uiFont
            font.pixelSize: Math.max(8, Math.min(parent.width, parent.height) * 0.62)
            font.bold: true
          }
        }
      }
    }
  }
}
