const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.resolve(__dirname, "..", "index.html"), "utf8");
const required = [
  'data-composition-id="floattrip-xiaohongshu"',
  'data-duration="24"',
  'window.__timelines["floattrip-xiaohongshu"]',
  'gsap.timeline({ paused: true })',
];
const missing = required.filter(token => !html.includes(token));
const forbidden = ["repeat: -1", "Math.random()", "Date.now()", "video.play()", "audio.play()"];
const foundForbidden = forbidden.filter(token => html.includes(token));
const sceneCount = (html.match(/<section id="scene\d"/g) || []).length;
const transitionCount = (html.match(/const at = i \* 4/g) || []).length;
const entranceCalls = (html.match(/enter\(newScene/g) || []).length + (html.match(/enter\("#scene1"/g) || []).length;
if (missing.length || foundForbidden.length || sceneCount !== 6 || transitionCount !== 1 || entranceCalls !== 2) {
  console.error(JSON.stringify({ missing, foundForbidden, sceneCount, transitionLoop: transitionCount, entranceCallSites: entranceCalls }, null, 2));
  process.exit(1);
}
console.log("Timeline audit passed: 6 scenes, 24 seconds, five timed transitions, synchronous paused timeline, entrance choreography, no forbidden nondeterminism or infinite repeats.");
