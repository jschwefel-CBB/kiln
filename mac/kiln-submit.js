#!/usr/bin/env osascript -l JavaScript
//
// kiln-submit.js — Mac-side handoff for the kiln pipeline (JXA / osascript).
//
// Packages a Final Cut Pro export as a kiln job and drops it into deb005's SMB inbox:
//   1. derive a filesystem-safe job_id,
//   2. build job.json from a preset (+ optional overrides),
//   3. ATOMICALLY place master + job.json in the inbox (stage in <inbox>/.staging/<id>/
//      then rename into place — deb005 skips dot-dirs, so a half-copied job is never seen),
//   4. (optional) ping deb005's submit endpoint so it picks up immediately.
//
// Invisible in daily use: an FCP Compressor "Run Automator Workflow" job action runs it via
//   /usr/bin/osascript "$HOME/bin/kiln-submit.js" "$1" --preset standard
// Written in JXA because osascript is ALWAYS present on macOS (system `python3` is an Xcode
// CLT stub that prompts to install). See docs/specs/2026-07-03-mac-controller-spec.md.
//
// Usage:
//   osascript kiln-submit.js MASTER [--preset NAME] [--job-id ID] [--title T]
//        [--inbox PATH] [--submit-url URL] [--dry-run]
//        [--transcode true|false] [--captions true|false] [--normalize true|false]
//        [--chapters true|false] [--metadata true|false] [--upscale true|false]
//        [--codec auto|hevc|h264] [--target-lufs N]
//        [--whisper-model NAME] [--llm-model NAME] [--keep-master true|false]

ObjC.import('Foundation');
// JXA has no built-in exit; bind libc exit(3) so die() can set a specific status code.
// (`$.exit` does not exist on the ObjC bridge — calling it throws a TypeError.)
ObjC.bindFunction('exit', ['void', ['int']]);

// --- presets: each sets the `jobs` toggles (see spec §4) ---
var PRESETS = {
  'standard':        { transcode: true,  captions: true,  normalize: true,  chapters: true,  metadata: true,  upscale: false },
  '4k':              { transcode: true,  captions: true,  normalize: true,  chapters: true,  metadata: true,  upscale: false },
  'upscale':         { transcode: true,  captions: true,  normalize: true,  chapters: true,  metadata: true,  upscale: true  },
  'transcode-only':  { transcode: true,  captions: false, normalize: true,  chapters: false, metadata: false, upscale: false },
};
var STEP_KEYS = ['transcode', 'captions', 'normalize', 'chapters', 'metadata', 'upscale'];
var OPTION_KEYS = ['codec', 'target_lufs', 'whisper_model', 'llm_model', 'keep_master'];
var MASTER_EXTS = ['.mov', '.mp4', '.mxf', '.mkv'];

function die(msg, code) {
  var err = $.NSFileHandle.fileHandleWithStandardError;
  err.writeData($.NSString.alloc.initWithUTF8String('kiln-submit: ' + msg + '\n')
                .dataUsingEncoding($.NSUTF8StringEncoding));
  $.exit(code === undefined ? 2 : code);
}

function log(msg) { console.log(msg); }  // JXA console.log -> stderr; fine for status

// --- tiny arg parser: first non---- token is MASTER; then --key value / bare boolean flags ---
function parseArgs(argv) {
  var out = { _positional: [], flags: {} };
  var BOOL_FLAGS = { '--dry-run': true };  // flags that take no value
  for (var i = 0; i < argv.length; i++) {
    var a = argv[i];
    if (a.indexOf('--') === 0) {
      var key = a.slice(2);
      if (BOOL_FLAGS['--' + key]) { out.flags[key] = 'true'; continue; }
      // --keep-master with no following value is treated as true
      var next = argv[i + 1];
      if (next === undefined || next.indexOf('--') === 0) { out.flags[key] = 'true'; }
      else { out.flags[key] = next; i++; }
    } else {
      out._positional.push(a);
    }
  }
  return out;
}

function asBool(v, dflt) {
  if (v === undefined) return dflt;
  return String(v).toLowerCase() === 'true' || String(v) === '1' || String(v).toLowerCase() === 'yes';
}

// --- job_id slug: lowercase, spaces->-, keep [a-z0-9._-], collapse/trim, cap length ---
function slug(x) {
  var s = String(x).toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9._-]/g, '');
  s = s.replace(/[-._]{2,}/g, function (m) { return m[0]; });   // collapse repeats
  s = s.replace(/^[-._]+/, '').replace(/[-._]+$/, '');           // trim edges
  return s.slice(0, 60);
}

function todayISO() {
  var fmt = $.NSDateFormatter.alloc.init;
  fmt.dateFormat = 'yyyy-MM-dd';
  return ObjC.unwrap(fmt.stringFromDate($.NSDate.date));
}

function basenameNoExt(path) {
  var ns = $.NSString.alloc.initWithUTF8String(path);
  return ObjC.unwrap(ns.lastPathComponent.stringByDeletingPathExtension);
}

function fileExists(path) {
  return $.NSFileManager.defaultManager.fileExistsAtPath(path);
}

function isDirWritable(path) {
  var fm = $.NSFileManager.defaultManager;
  var isDir = Ref();
  return fm.fileExistsAtPathIsDirectory(path, isDir) && isDir[0] && fm.isWritableFileAtPath(path);
}

function readConfig() {
  var home = ObjC.unwrap($.NSHomeDirectory());
  var cfgPath = home + '/.config/kiln/submit.json';
  if (!fileExists(cfgPath)) return {};
  var data = $.NSData.dataWithContentsOfFile(cfgPath);
  if (!data) return {};
  var obj = $.NSJSONSerialization.JSONObjectWithDataOptionsError(data, 0, null);
  return obj ? ObjC.deepUnwrap(obj) : {};
}

function writeText(path, text) {
  var ns = $.NSString.alloc.initWithUTF8String(text);
  return ns.writeToFileAtomicallyEncodingError(path, true, $.NSUTF8StringEncoding, null);
}

function copyFile(src, dst) {
  var fm = $.NSFileManager.defaultManager;
  if (fileExists(dst)) fm.removeItemAtPathError(dst, null);
  return fm.copyItemAtPathToPathError(src, dst, null);
}

// atomic within one filesystem: rename the fully-staged folder into the inbox
function renameDir(src, dst) {
  var fm = $.NSFileManager.defaultManager;
  if (fileExists(dst)) fm.removeItemAtPathError(dst, null);
  return fm.moveItemAtPathToPathError(src, dst, null);
}

function mkdirs(path) {
  // attributes: must be a real nil ($()), NOT JS null — JXA marshals `null` into an
  // NSNull, and createDirectory then calls -count on it -> "-[NSNull count]: unrecognized
  // selector". (The trailing error: out-param tolerates null; the attributes: input does not.)
  return $.NSFileManager.defaultManager
    .createDirectoryAtPathWithIntermediateDirectoriesAttributesError(path, true, $(), null);
}

function ping(submitUrl) {
  if (!submitUrl) return;
  try {
    var app = Application.currentApplication();
    app.includeStandardAdditions = true;
    app.doShellScript('curl -s -m 3 -X POST ' + quoteShell(submitUrl) + ' >/dev/null 2>&1 || true');
  } catch (e) { /* best-effort; deb005 still polls within ~2s */ }
}

function quoteShell(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'"; }

function run(argv) {
  var args = parseArgs(argv);
  var cfg = readConfig();

  var master = args._positional[0];
  if (!master) die('missing MASTER (the exported file path)', 2);
  if (!fileExists(master)) die('master not found: ' + master, 2);

  // job_id
  var rawId = args.flags['job-id'] || (todayISO() + '_' + basenameNoExt(master));
  var jobId = slug(rawId);
  if (!jobId) die('could not derive a safe job_id from: ' + rawId, 2);

  // preset -> jobs toggles, then per-step overrides
  var presetName = args.flags['preset'] || cfg.default_preset || 'standard';
  var preset = PRESETS[presetName];
  if (!preset) die('unknown preset: ' + presetName + ' (have: ' + Object.keys(PRESETS).join(', ') + ')', 2);
  var jobs = {};
  STEP_KEYS.forEach(function (k) { jobs[k] = preset[k]; });
  STEP_KEYS.forEach(function (k) {
    if (args.flags[k] !== undefined) jobs[k] = asBool(args.flags[k], jobs[k]);
  });

  // options (only include keys actually provided)
  var options = {};
  if (args.flags['codec'] !== undefined)         options.codec = args.flags['codec'];
  if (args.flags['target-lufs'] !== undefined)   options.target_lufs = parseInt(args.flags['target-lufs'], 10);
  if (args.flags['whisper-model'] !== undefined) options.whisper_model = args.flags['whisper-model'];
  if (args.flags['llm-model'] !== undefined)     options.llm_model = args.flags['llm-model'];
  if (args.flags['keep-master'] !== undefined)   options.keep_master = asBool(args.flags['keep-master'], false);

  var masterName = 'master' + extOf(master);
  var job = { job_id: jobId, source: masterName, jobs: jobs };
  if (args.flags['title']) job.title = args.flags['title'];
  if (Object.keys(options).length) job.options = options;

  var jobJson = JSON.stringify(job, null, 2);

  var inbox = args.flags['inbox'] || cfg.inbox;
  var submitUrl = (args.flags['submit-url'] !== undefined) ? args.flags['submit-url'] : (cfg.submit_url || '');

  if (args.flags['dry-run']) {
    log('# DRY RUN — nothing written');
    log('# job_id: ' + jobId);
    log('# inbox:  ' + (inbox || '(unset)'));
    log('# would place: ' + (inbox ? (inbox + '/' + jobId + '/') : '(no inbox)'));
    log(jobJson);
    return;
  }

  if (!inbox) die('no inbox configured (pass --inbox or set it in ~/.config/kiln/submit.json)', 2);
  if (!isDirWritable(inbox)) die('inbox not mounted/writable: ' + inbox, 2);

  // Atomic drop: stage under <inbox>/.staging/<jobId>/ (deb005 ignores dot-dirs), then rename.
  var stagingRoot = inbox + '/.staging';
  var stageDir = stagingRoot + '/' + jobId;
  var fm = $.NSFileManager.defaultManager;
  if (fileExists(stageDir)) fm.removeItemAtPathError(stageDir, null);
  if (!mkdirs(stageDir)) die('could not create staging dir: ' + stageDir, 1);

  if (!copyFile(master, stageDir + '/' + masterName)) die('failed to copy master into staging', 1);
  if (!writeText(stageDir + '/job.json', jobJson)) die('failed to write job.json', 1);

  var finalDir = inbox + '/' + jobId;
  if (!renameDir(stageDir, finalDir)) die('failed to move job into inbox (same filesystem?)', 1);

  ping(submitUrl);

  log(jobId);
  log(finalDir);
}

function extOf(path) {
  var ns = $.NSString.alloc.initWithUTF8String(path);
  var ext = ObjC.unwrap(ns.pathExtension);
  return ext ? ('.' + ext.toLowerCase()) : '.mov';
}
