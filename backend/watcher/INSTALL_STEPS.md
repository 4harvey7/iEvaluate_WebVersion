# Scan Watcher install steps (source of truth for the website)

Same wording as `HOW TO INSTALL.txt` inside the package. If you change one,
change the other. Button/label words in quotes must match the real UI.

Note for the website: the zip has a top folder `ScanWatcher/`, so after
"Extract All" the user first sees a folder named **ScanWatcher** and must open
it before they can see **INSTALL** (step 3 says so).

## Main steps

1. On the website, open **"Scanner setup"**. Choose your scanner station. Click **"Download installer"**.
2. Open your **Downloads** folder. Right-click the **ScanWatcher** file. Click **"Extract All"**, then **"Extract"**.
3. In the new window, double-click the **"ScanWatcher"** folder, then double-click **"INSTALL"**.
   - If you see "Windows protected your PC": click **"More info"**, then **"Run anyway"**.
   - If you see "Open File - Security Warning": click **"Run"**.
   - If you see "Do you want to allow this app to make changes": click **"Yes"**.
4. Wait. Do not type anything. When the window turns green and says **"ALL DONE"**, close the window.
5. In your scanner program, look for **"Save to"**, **"Destination"** or **"Folder"**. Choose **C:\Scans**.
6. Look at the website. The light should be green (**"Online"**). Scan one page to test it.

## How do I know it works?

Start a batch for this station on the website, scan one page, wait one minute: the page shows up on the website.

## Something went wrong?

| If you see | Do this |
|---|---|
| Red light "Offline" | Turn the laptop on and connect it to the internet. Wait 1 minute. Still red? Restart the laptop. |
| "Folder not found" | On the website, change the folder to C:\Scans. Wait 1 minute. |
| "That folder isn't allowed" | The folder is too big (like the whole Desktop). On the website, change it to C:\Scans. |
| The window closes right away | Right-click the ScanWatcher file, click "Extract All" first, then double-click INSTALL in the new folder. |
| The window says "STOP" / is red | Read the numbered lines in the window and do them. |
| No internet | Connect the laptop to the internet. Double-click INSTALL again. |
| Scans do not show up | Your scanner saves somewhere else. In the scanner program, set the save folder to C:\Scans. |
| Not sure | Double-click "CHECK" in the ScanWatcher folder. It says what is wrong. |

## Change the scan folder

On the website only. Change the folder and save. The laptop switches by itself within 30 seconds. Then set the scanner to save into the new folder.

## Moving to a new laptop

Do steps 1-6 on the new laptop. The old laptop stops sending scans (every new download makes a new key). Only download again for a new or replaced laptop.

## JSON (same content, for code)

```json
{
  "steps": [
    "On the website, open \"Scanner setup\". Choose your scanner station. Click \"Download installer\".",
    "Open your Downloads folder. Right-click the ScanWatcher file. Click \"Extract All\", then \"Extract\".",
    "In the new window, double-click the \"ScanWatcher\" folder, then double-click \"INSTALL\".",
    "Wait. Do not type anything. When the window turns green and says \"ALL DONE\", close the window.",
    "In your scanner program, look for \"Save to\", \"Destination\" or \"Folder\". Choose C:\\Scans.",
    "Look at the website. The light should be green (\"Online\"). Scan one page to test it."
  ],
  "windows_warnings": [
    { "see": "Windows protected your PC", "do": "Click \"More info\", then \"Run anyway\"." },
    { "see": "Open File - Security Warning", "do": "Click \"Run\"." },
    { "see": "Do you want to allow this app to make changes", "do": "Click \"Yes\"." }
  ],
  "troubleshooting": [
    { "see": "Red light \"Offline\"", "do": "Turn the laptop on and connect it to the internet. Wait 1 minute. Still red? Restart the laptop." },
    { "see": "\"Folder not found\"", "do": "On the website, change the folder to C:\\Scans. Wait 1 minute." },
    { "see": "\"That folder isn't allowed\"", "do": "The folder is too big (like the whole Desktop). On the website, change it to C:\\Scans." },
    { "see": "The window closes right away", "do": "Right-click the ScanWatcher file, click \"Extract All\" first, then double-click INSTALL in the new folder." },
    { "see": "No internet", "do": "Connect the laptop to the internet. Double-click INSTALL again." },
    { "see": "Scans do not show up", "do": "Your scanner saves somewhere else. In the scanner program, set the save folder to C:\\Scans." },
    { "see": "Not sure", "do": "Double-click \"CHECK\" in the ScanWatcher folder. It says what is wrong." }
  ]
}
```
