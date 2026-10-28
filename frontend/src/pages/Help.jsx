import React from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components.jsx";
import { InstallSteps, ScannerStatus, useStation } from "../scanner.jsx";

// Same advice as the printed guide's "Something went wrong?" table.
const TROUBLE = [
  {
    q: "Red light \"Offline\"",
    a: (
      <>
        The Scan Watcher isn't running. On the scanner laptop, double-click <strong>Start Scan Watcher</strong> (on the
        desktop or in the Start menu). Wait 1 minute. Still red? Check the laptop is on and connected to the internet.
      </>
    ),
  },
  {
    q: "\"Folder not found\"",
    a: (
      <>
        On the website, change the folder to <strong>C:\Scans</strong> on the <Link to="/scanner-setup">Scanner setup</Link>{" "}
        page and click <strong>Save folder</strong>. Wait 1 minute.
      </>
    ),
  },
  {
    q: "\"That folder isn't allowed\"",
    a: (
      <>
        The folder is too big (like the whole Desktop). On the website, change it to <strong>C:\Scans</strong> on the{" "}
        <Link to="/scanner-setup">Scanner setup</Link> page and click <strong>Save folder</strong>. The laptop waits until
        you do.
      </>
    ),
  },
  {
    q: "\"Can't open the folder\"",
    a: (
      <>
        The laptop isn't allowed to open that folder. On the <Link to="/scanner-setup">Scanner setup</Link> page, change it
        to <strong>C:\Scans</strong> and click <strong>Save folder</strong>.
      </>
    ),
  },
  {
    q: "The install window closes right away",
    a: (
      <>
        Right-click the <strong>ScanWatcher</strong> file, click <strong>"Extract All"</strong> first, then double-click{" "}
        <strong>INSTALL</strong> in the new folder.
      </>
    ),
  },
  {
    q: "The install window says \"STOP\" or is red",
    a: <>Read the numbered lines in the window and do them.</>,
  },
  {
    q: "No internet",
    a: <>Connect the laptop to the internet. Double-click <strong>INSTALL</strong> again.</>,
  },
  {
    q: "Scans do not show up",
    a: (
      <>
        Your scanner saves somewhere else. In the scanner program, set the save folder to <strong>C:\Scans</strong>. Also
        check that a batch is started: pages scanned before you click <strong>Start scanning</strong> are held back (see
        "Scanned before this batch started" below). If the page says "Waiting to send", the laptop is catching up and
        will send them shortly.
      </>
    ),
  },
  {
    q: "Not sure what's wrong",
    a: (
      <>
        On the scanner laptop, double-click <strong>"CHECK"</strong> in the ScanWatcher folder. It says what is wrong.
      </>
    ),
  },
];

const FAQ = [
  {
    q: "How do I change the scan folder?",
    a: (
      <>
        Open <Link to="/scanner-setup">Scanner setup</Link>, type the full folder path (like C:\Scans\) and click{" "}
        <strong>Save folder</strong>. The page checks it on the laptop within about 30 seconds. The folder must already
        exist on the scanner laptop (C:\Scans\ is made at install). Then set the scanner to save into the same folder.
      </>
    ),
  },
  {
    q: "We have a new laptop. What do I do?",
    a: (
      <>
        Do install steps 1 to 6 on the new laptop. The old laptop stops sending scans, because every new download makes a
        new connection code. Only download again for a new or replaced laptop.
      </>
    ),
  },
  {
    q: "Windows says \"Windows protected your PC\".",
    a: (
      <>
        That's normal for this installer. Click <strong>"More info"</strong>, then <strong>"Run anyway"</strong>.
      </>
    ),
  },
  {
    q: "What does \"Needs a look\" mean?",
    a: (
      <>
        The sheet was scored, but something looked unusual (for example a smudge, or a sheet for a different
        instructor). A person should check it on the usual correction screen.
      </>
    ),
  },
  {
    q: "What does \"Already scanned\" mean?",
    a: <>The same page was received twice, so the second copy was ignored. Nothing to do.</>,
  },
  {
    q: "Can I copy scans into the scan folder instead of scanning?",
    a: (
      <>
        Yes. Files copied or moved into the scan folder now work the same as freshly scanned pages. Start a batch first,
        then copy the files in. They show up on the batch page within a few seconds.
      </>
    ),
  },
  {
    q: "What does \"Scanned before this batch started\" mean?",
    a: (
      <>
        The laptop found pages in the scan folder that were there before you clicked <strong>Start scanning</strong>. It
        holds them back so they don't end up in the wrong batch. The batch page shows a box with how many there are.
        Click <strong>Add to this batch</strong> if they belong to the stack you're scanning, or <strong>Discard</strong>{" "}
        if they don't. Discarded pages aren't marked; they're kept on the laptop in a "discarded" folder. The laptop
        does what you chose within 30 seconds.
      </>
    ),
  },
  {
    q: "What does \"Marking is switched off\" mean?",
    a: (
      <>
        This server isn't marking pages right now (for example, a test or preview server). Pages still arrive and are
        saved, and they show as <strong>Waiting for marking</strong>. They are marked when marking is turned on. There's
        nothing to fix on the scanner laptop. If you see this on the real site, tell IT.
      </>
    ),
  },
  {
    q: "Someone forgot to close yesterday's batch.",
    a: (
      <>
        Open the batch (it's the next step on <Link to="/">Home</Link>) and click <strong>Finish and close batch</strong>.
        If it has been quiet for a few hours you can also use <strong>Close a forgotten batch</strong>.
      </>
    ),
  },
];

export default function Help({ stationId, onStationMissing }) {
  const { station } = useStation(stationId, { onMissing: onStationMissing });

  return (
    <div>
      <PageHeader
        title="Help"
        subtitle="Simple steps for installing the scanner laptop and for everyday scanning, plus answers to common questions."
        back={{ to: "/", label: "Home" }}
      />

      <section className="section" aria-labelledby="help-install">
        <h2 id="help-install">Install the scanner laptop (once)</h2>
        <p>Do this on the laptop that is connected to the scanner.</p>
        <div className="status-strip">
          <span className="muted">Right now:</span>
          <ScannerStatus station={station} />
        </div>
        <InstallSteps station={station} where="help" />
        <h3>How do I know it works?</h3>
        <p>Start a batch for this station on the website, scan one page, and wait one minute. The page shows up on the website.</p>
      </section>

      <section className="section" aria-labelledby="help-daily">
        <h2 id="help-daily">Every day</h2>
        <ol className="steps plain">
          <li><span className="step-num" aria-hidden="true">1</span><div className="step-text">Double-click <strong>Start Scan Watcher</strong> on the scanner laptop (on the desktop or in the Start menu).</div></li>
          <li><span className="step-num" aria-hidden="true">2</span><div className="step-text">Log in. On <Link to="/">Home</Link>, check that the scanner laptop light is green.</div></li>
          <li><span className="step-num" aria-hidden="true">3</span><div className="step-text">Click <Link to="/start">Start a batch</Link>. Choose the instructor and the subject, then click <strong>Start scanning</strong>.</div></li>
          <li><span className="step-num" aria-hidden="true">4</span><div className="step-text">Put the sheets in the scanner and scan them. Each page shows up on the screen within a few seconds.</div></li>
          <li><span className="step-num" aria-hidden="true">5</span><div className="step-text">Look at anything marked <strong>Needs a look</strong>, <strong>Rejected</strong> or <strong>Failed</strong>. Scan rejected or failed sheets again.</div></li>
          <li><span className="step-num" aria-hidden="true">6</span><div className="step-text">When the stack is done, click <strong>Finish and close batch</strong>. Then start the next batch.</div></li>
          <li><span className="step-num" aria-hidden="true">7</span><div className="step-text">When you're finished for the day, click <strong>Log out</strong>. You can also double-click <strong>Stop Scan Watcher</strong> on the scanner laptop.</div></li>
        </ol>
      </section>

      <section className="section" aria-labelledby="help-trouble">
        <h2 id="help-trouble">Something went wrong?</h2>
        <p>Click a problem to see what to do.</p>
        <div className="faq">
          {TROUBLE.map((item) => (
            <details key={item.q}>
              <summary>{item.q}</summary>
              <div className="faq-answer">{item.a}</div>
            </details>
          ))}
        </div>
      </section>

      <section className="section" aria-labelledby="help-faq">
        <h2 id="help-faq">Common questions</h2>
        <div className="faq">
          {FAQ.map((item) => (
            <details key={item.q}>
              <summary>{item.q}</summary>
              <div className="faq-answer">{item.a}</div>
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}
