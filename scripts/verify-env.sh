#!/usr/bin/env bash
# What the app verifier can drive on this machine, and one-command setup for what is missing.
#
#   verify-env.sh check [DIR]          -> JSON: platforms the repo at DIR looks like + tools ready per platform
#   verify-env.sh setup web|extension|android|ios
#
# Everything is free and local:
#   web, extension -> Playwright (+ its Chromium) in ~/.feature-factory/verify (shared by all repos)
#   android        -> Android SDK (adb, emulator, an AVD) + Maestro (needs Java 17+)
#   ios            -> Xcode simulator (xcrun simctl) + Maestro (needs Java 17+)
# Java is found in: $JAVA_HOME, ~/.feature-factory/jdk, Android Studio's bundled runtime, Homebrew,
# /usr/libexec/java_home. If none is 17+, setup downloads Eclipse Temurin 17 into ~/.feature-factory/jdk
# (no Homebrew or admin rights needed).
set -uo pipefail
VDIR="$HOME/.feature-factory/verify"
PW_VERSION="1.56.1"
sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
export PATH="$PATH:$sdk/platform-tools:$sdk/emulator:$HOME/.maestro/bin"
JDK_DIR="$HOME/.feature-factory/jdk"
java_major() { "$1/bin/java" -version 2>&1 | sed -nE '1s/.*version "([0-9]+).*/\1/p'; }
find_java() {
  local h cands=("${JAVA_HOME:-}" "$JDK_DIR/Contents/Home" "$JDK_DIR"
    "/Applications/Android Studio.app/Contents/jbr/Contents/Home"
    /opt/homebrew/opt/openjdk@17 /opt/homebrew/opt/openjdk /usr/local/opt/openjdk@17)
  [ -x /usr/libexec/java_home ] && cands+=("$(/usr/libexec/java_home -v 17+ 2>/dev/null)")
  for h in "${cands[@]}"; do
    [ -n "$h" ] && [ -x "$h/bin/java" ] && [ "$(java_major "$h")" -ge 17 ] 2>/dev/null && { echo "$h"; return 0; }
  done
  return 1
}
if jh=$(find_java); then export JAVA_HOME="$jh" PATH="$jh/bin:$PATH"; fi

has() { command -v "$1" >/dev/null 2>&1; }
java_ok() { [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ]; }
pw_ok() { [ -d "$VDIR/node_modules/playwright" ] && (cd "$VDIR" && node -e 'require("playwright").chromium.executablePath()' >/dev/null 2>&1) \
  && [ -e "$(cd "$VDIR" && node -e 'process.stdout.write(require("playwright").chromium.executablePath())')" ]; }

detect() {  # platforms the repo looks like it targets
  local d="$1" out=()
  [ -f "$d/package.json" ] && grep -Eq '"(next|vite|react-scripts|@angular/core|vue|svelte|@sveltejs/kit|nuxt|astro|remix|@remix-run/react|gatsby|webpack-dev-server|parcel)"' "$d/package.json" && out+=(web)
  find "$d" -maxdepth 3 -name manifest.json -not -path '*/node_modules/*' -not -path '*/.ff-worktrees/*' 2>/dev/null \
    | xargs grep -l '"manifest_version"' 2>/dev/null | grep -q . && out+=(extension)
  { [ -f "$d/android/app/build.gradle" ] || [ -f "$d/android/app/build.gradle.kts" ] || [ -f "$d/app/build.gradle" ] || [ -f "$d/app/build.gradle.kts" ]; } && out+=(android)
  { find "$d" -maxdepth 3 \( -name '*.xcodeproj' -o -name '*.xcworkspace' \) -not -path '*/Pods/*' -not -path '*/node_modules/*' 2>/dev/null | grep -q .; } && out+=(ios)
  [ ${#out[@]} -eq 0 ] && { [ -f "$d/index.html" ] || [ -d "$d/public" ]; } && out+=(web)
  [ ${#out[@]} -gt 0 ] && printf "%s\n" "${out[@]}"; return 0
}

check() {
  local d="${1:-$PWD}" plats avds sims
  plats=$(detect "$d" | jq -R . | jq -sc .)
  avds=$(has emulator && emulator -list-avds 2>/dev/null | grep -v '^INFO' | jq -R . | jq -sc . || echo '[]')
  sims=$(has xcrun && xcrun simctl list devices available -j 2>/dev/null | jq -c '[.devices | to_entries[] | select(.key|test("iOS")) | .value[] | .name] | unique' || echo '[]')
  jq -n --argjson plats "$plats" --argjson avds "$avds" --argjson sims "$sims" \
    --arg pw "$(pw_ok && echo yes || echo no)" --arg adb "$(has adb && echo yes || echo no)" \
    --arg maestro "$(has maestro && echo yes || echo no)" --arg java "$(java_ok && echo yes || echo no)" \
    --arg simctl "$(has xcrun && xcrun simctl help >/dev/null 2>&1 && echo yes || echo no)" --arg vdir "$VDIR" --arg sdk "$sdk" --arg jh "${JAVA_HOME:-}" '{
    repo_platforms: $plats,
    web:       {ready: ($pw == "yes"), playwright_dir: $vdir, setup: "verify-env.sh setup web"},
    extension: {ready: ($pw == "yes"), playwright_dir: $vdir, setup: "verify-env.sh setup extension"},
    android:   {ready: ($adb == "yes" and ($avds|length) > 0 and $maestro == "yes" and $java == "yes"),
                adb: ($adb == "yes"), avds: $avds, maestro: ($maestro == "yes"), java: ($java == "yes"),
                sdk: $sdk, fallback: (if $adb == "yes" and ($avds|length) > 0 then "adb only (uiautomator dump + input tap)" else null end),
                setup: "verify-env.sh setup android"},
    ios:       {ready: ($simctl == "yes" and ($sims|length) > 0 and $maestro == "yes" and $java == "yes"),
                simctl: ($simctl == "yes"), simulators: $sims, maestro: ($maestro == "yes"), java: ($java == "yes"),
                fallback: (if $simctl == "yes" and ($sims|length) > 0 then "simctl only (launch, open URL, screenshots; no taps)" else null end),
                setup: "verify-env.sh setup ios"},
    path_hint: ((if $jh != "" then "export JAVA_HOME=\"\($jh)\"; " else "" end) + "export PATH=\"\(if $jh != "" then $jh + "/bin:" else "" end)$PATH:\($sdk)/platform-tools:\($sdk)/emulator:$HOME/.maestro/bin\"")
  }'
}

setup_pw() {
  mkdir -p "$VDIR" && cd "$VDIR" || exit 1
  [ -f package.json ] || echo '{"name":"ff-verify","private":true}' > package.json
  npm install --silent --no-audit --no-fund "playwright@$PW_VERSION" && npx --yes "playwright@$PW_VERSION" install chromium
}
setup_java() {
  java_ok && return 0
  local arch; arch=$(uname -m); [ "$arch" = "arm64" ] && arch=aarch64; [ "$arch" = "x86_64" ] && arch=x64
  local os=mac; [ "$(uname -s)" = "Linux" ] && os=linux
  echo "Downloading Java 17 (Eclipse Temurin) into $JDK_DIR ..." >&2
  rm -rf "$JDK_DIR" && mkdir -p "$JDK_DIR" || return 1
  curl -fsSL "https://api.adoptium.net/v3/binary/latest/17/ga/$os/$arch/jdk/hotspot/normal/eclipse" \
    | tar -xz -C "$JDK_DIR" --strip-components 1 || { echo "Java download failed." >&2; return 1; }
  jh=$(find_java) && export JAVA_HOME="$jh" PATH="$jh/bin:$PATH"
}
setup_maestro() {
  setup_java || return 1
  echo "Using Java at $JAVA_HOME" >&2
  if ! has maestro; then
    curl -fsSL "https://get.maestro.mobile.dev" | bash || { echo "Maestro install failed." >&2; return 1; }
  fi
  maestro --version >&2
}

case "${1:-}" in
  check) check "${2:-$PWD}" ;;
  setup)
    case "${2:-}" in
      web|extension) setup_pw ;;
      android)
        has adb || { echo "Android SDK not found at $sdk. Install Android Studio (or set ANDROID_HOME), create an emulator in Device Manager, then re-run." >&2; exit 1; }
        setup_maestro
        has emulator && [ -z "$(emulator -list-avds 2>/dev/null | grep -v '^INFO')" ] && echo "No Android emulator yet: create one in Android Studio > Device Manager." >&2 ;;
      ios)
        has xcrun || { echo "Install Xcode from the App Store first." >&2; exit 1; }
        setup_maestro ;;
      *) echo "usage: verify-env.sh setup web|extension|android|ios" >&2; exit 1 ;;
    esac
    echo "--- now:"; check "$PWD" ;;
  *) sed -n '2,11p' "$0"; exit 1 ;;
esac
