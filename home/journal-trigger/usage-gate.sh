#!/usr/bin/env bash
# usage-gate.sh -- decide whether the journal trigger may fire, based on
# current Claude plan usage.
#
#   exit 0  -> gate PASSES (under budget, ok to fire)
#   exit 1  -> gate FAILS  (over pace / over ceiling / any error -> fail closed)
#
# Reads the OAuth access token from ~/.claude/.credentials.json and queries the
# undocumented usage endpoint. The stored access token is short-lived (~8h) and
# is only refreshed when an interactive Claude Code session runs; on the idle
# mornings this gate exists to catch, it has usually expired -> the endpoint 401s.
# So the gate now SELF-REFRESHES: if the token is expired (or the usage call
# 401s), it exchanges the stored refresh token for a new one and retries once,
# writing the rotated pair back to ~/.claude/.credentials.json atomically. This
# mirrors how concurrent Claude Code sessions already share+rotate that file, and
# only runs at 01-06 PT when no interactive session is awake to race it.
# Any OTHER non-200 (429, 5xx, shape change) still fails CLOSED with a logged warning.
#
# Manual ops: `usage-gate.sh --refresh` forces a token refresh and reports, without
# touching the gate decision (useful to re-prime the credential by hand).
#
# Field mapping (confirmed live against the real response, not guessed):
#   .seven_day.utilization  -> weekly %      (0-100 scale)
#   .five_hour.utilization   -> 5-hour %      (0-100 scale)
#   .seven_day.resets_at     -> NEXT weekly reset (future). Window start is
#                               resets_at - 168h, so hours-into-week =
#                               168 - hours_until(resets_at).
#
# Gate passes iff:  weekly_pct < weekly_target   AND   five_hour_pct < 80
#   weekly_target = 0.5 * hours_into_week              (normal half-rate burn)
#   weekly_target = 95 (flat)  in the last 5h before the weekly reset
#       ("use it or lose it": fill the weekly budget that would otherwise reset
#        unused. The 5-hour ceiling still applies, so the burst can't spike.)
#
# Prints one machine-readable summary line to stdout (consumed/logged by the
# caller). With --raw, also dumps the raw JSON body to stderr.
#
# Usage: usage-gate.sh [--raw]

set -u

CRED="${HOME}/.claude/.credentials.json"
USAGE_URL="https://api.anthropic.com/api/oauth/usage"
TOKEN_URL="https://api.anthropic.com/v1/oauth/token"
# Public Claude Code OAuth client id (ships in the distributed CLI; NOT a secret).
OAUTH_CLIENT_ID="9d1c250a-e61b-44d9-88ed-5944d1962f5e"
EXPIRY_BUFFER=120          # refresh proactively when within this many seconds of expiry
LOG="${HOME}/.local/state/journal-cron.log"

FIVE_HOUR_CEIL=80          # 5-hour utilization ceiling (percent)
WEEKLY_SLOPE=0.5           # normal: allowed weekly percent per hour-into-window
WEEK_HOURS=168             # 7-day window length
WEEKLY_FINAL_WINDOW_H=5    # "use it or lose it" window before the weekly reset
WEEKLY_FINAL_CEIL=95       # in that window, lift the weekly allowance to this flat %

RAW=0
REFRESH_ONLY=0
case "${1:-}" in
  --raw)     RAW=1 ;;
  --refresh) REFRESH_ONLY=1 ;;
esac

mkdir -p "$(dirname "$LOG")"
warn() { printf '%s [usage-gate] WARN %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }
note() { printf '%s [usage-gate] INFO %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }

# Exchange the stored refresh token for a fresh access/refresh pair and write the
# result back to $CRED atomically (temp file + mv, mode 600, .bak kept). Updates
# the global TOKEN on success. Returns 0/1; never aborts the caller. A FAILED
# request does not rotate anything server-side, so retrying later is safe.
do_refresh() {
  local rt resp new_at new_rt exp_in new_exp tmp
  rt=$(jq -r '.claudeAiOauth.refreshToken // empty' "$CRED" 2>/dev/null)
  [ -z "$rt" ] && { warn "refresh: no refresh token in $CRED"; return 1; }

  resp=$(curl -sS -m 30 -X POST "$TOKEN_URL" \
    -H 'content-type: application/json' \
    -d "$(jq -nc --arg rt "$rt" --arg cid "$OAUTH_CLIENT_ID" \
            '{grant_type:"refresh_token", refresh_token:$rt, client_id:$cid}')" \
    2>>"$LOG") || { warn "refresh: curl failed"; return 1; }

  new_at=$(printf '%s' "$resp" | jq -r '.access_token  // empty' 2>/dev/null)
  new_rt=$(printf '%s' "$resp" | jq -r '.refresh_token // empty' 2>/dev/null)
  exp_in=$(printf '%s' "$resp" | jq -r '.expires_in    // empty' 2>/dev/null)
  if [ -z "$new_at" ] || [ -z "$new_rt" ]; then
    warn "refresh: bad response: $(printf '%s' "$resp" | tr -d '\n' | head -c 160)"
    return 1
  fi
  [ -z "$exp_in" ] && exp_in=28800   # default 8h if the server omits expires_in
  new_exp=$(( ( $(date +%s) + exp_in ) * 1000 ))

  tmp="${CRED}.tmp.$$"
  if ! jq --arg at "$new_at" --arg rt "$new_rt" --argjson exp "$new_exp" \
        '.claudeAiOauth.accessToken=$at
         | .claudeAiOauth.refreshToken=$rt
         | .claudeAiOauth.expiresAt=$exp' \
        "$CRED" >"$tmp" 2>>"$LOG"; then
    warn "refresh: jq merge failed"; rm -f "$tmp"; return 1
  fi
  # Only swap in a file that is valid JSON and still carries a token.
  if ! jq -e '.claudeAiOauth.accessToken' "$tmp" >/dev/null 2>&1; then
    warn "refresh: validation failed, not swapping"; rm -f "$tmp"; return 1
  fi
  cp -p "$CRED" "${CRED}.bak" 2>/dev/null
  chmod 600 "$tmp"
  mv -f "$tmp" "$CRED" || { warn "refresh: atomic mv failed"; rm -f "$tmp"; return 1; }
  TOKEN="$new_at"
  note "refresh: token rotated ok (expires in ${exp_in}s)"
  return 0
}

# Fetch the usage endpoint with the current $TOKEN. Sets BODY/HTTP/JSON/CURL_RC.
fetch_usage() {
  BODY=$(curl -sS -m 20 -w $'\n%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    -H "anthropic-beta: oauth-2025-04-20" \
    -H "anthropic-version: 2023-06-01" \
    "$USAGE_URL" 2>>"$LOG")
  CURL_RC=$?
  HTTP=$(printf '%s' "$BODY" | tail -n1)
  JSON=$(printf '%s' "$BODY" | sed '$d')
}

# Emit a fail-closed summary line and exit nonzero.
fail_closed() { # $1 = reason
  echo "weekly_pct=NA target_pct=NA mode=NA five_hour_pct=NA hours_into_week=NA hours_until_reset=NA weekly_reset=NA gate_decision=fail gate_reason=$1"
  exit 1
}

# --- token ---------------------------------------------------------------
TOKEN=$(jq -r '.claudeAiOauth.accessToken // empty' "$CRED" 2>/dev/null)
if [ -z "$TOKEN" ]; then warn "no access token in $CRED"; fail_closed no_token; fi

# Manual `--refresh`: force a rotation, report, and exit (no gate decision).
if [ "$REFRESH_ONLY" = 1 ]; then
  if do_refresh; then echo "refresh: ok"; exit 0; else echo "refresh: failed (see $LOG)"; exit 1; fi
fi

# Proactive refresh: if the stored token is already expired (or within
# EXPIRY_BUFFER of it), rotate before we bother the usage endpoint.
EXP_MS=$(jq -r '.claudeAiOauth.expiresAt // empty' "$CRED" 2>/dev/null)
NOW_MS=$(( $(date +%s) * 1000 ))
if [ -n "$EXP_MS" ] && [ "$EXP_MS" -le $(( NOW_MS + EXPIRY_BUFFER * 1000 )) ]; then
  do_refresh || warn "proactive refresh failed; trying usage with existing token"
fi

# --- fetch (self-refresh once on 401) ------------------------------------
fetch_usage
# A 401 means the token died despite the expiry check (clock skew or server-side
# revocation). Refresh once and retry. Any other non-200 still fails closed.
if [ "$CURL_RC" -eq 0 ] && [ "$HTTP" = "401" ]; then
  warn "usage 401; attempting one refresh + retry"
  do_refresh && fetch_usage
fi

[ "$RAW" = 1 ] && printf '%s\n' "$JSON" >&2

if [ "$CURL_RC" -ne 0 ]; then warn "curl failed rc=$CURL_RC"; fail_closed "curl_rc_${CURL_RC}"; fi
if [ "$HTTP" != "200" ]; then warn "HTTP $HTTP from usage endpoint"; fail_closed "http_${HTTP}"; fi

# --- parse ---------------------------------------------------------------
IFS=$'\t' read -r weekly_pct five_pct weekly_reset <<EOF
$(printf '%s' "$JSON" | jq -r '[.seven_day.utilization, .five_hour.utilization, .seven_day.resets_at] | @tsv' 2>/dev/null)
EOF

if [ -z "${weekly_pct:-}" ] || [ "$weekly_pct" = "null" ] \
  || [ -z "${five_pct:-}" ] || [ "$five_pct" = "null" ] \
  || [ -z "${weekly_reset:-}" ] || [ "$weekly_reset" = "null" ]; then
  warn "unexpected response shape (missing fields)"; fail_closed bad_shape
fi

# --- compute weekly allowance --------------------------------------------
reset_epoch=$(date -d "$weekly_reset" +%s 2>/dev/null)
if [ -z "$reset_epoch" ]; then warn "could not parse reset ts: $weekly_reset"; fail_closed bad_ts; fi
now_epoch="${USAGE_NOW_EPOCH:-$(date -u +%s)}"   # USAGE_NOW_EPOCH: test-only override

# hours since window start (= reset - 168h), clamped [0,168]; and hours until reset
hours_since=$(awk -v r="$reset_epoch" -v n="$now_epoch" -v wh="$WEEK_HOURS" \
  'BEGIN{ s=r-wh*3600; h=(n-s)/3600; if(h<0)h=0; if(h>wh)h=wh; printf "%.2f", h }')
hours_until=$(awk -v r="$reset_epoch" -v n="$now_epoch" 'BEGIN{ printf "%.2f", (r-n)/3600 }')

# Effective weekly ceiling: normally the half-rate linear burn target. But in the
# final WEEKLY_FINAL_WINDOW_H hours before reset, lift it to a flat WEEKLY_FINAL_CEIL
# so the otherwise-wasted weekly budget fills ("use it or lose it"). The 5-hour
# ceiling still applies, which keeps the end-of-week burst from spiking.
if awk -v hu="$hours_until" -v w="$WEEKLY_FINAL_WINDOW_H" 'BEGIN{ exit !(hu>=0 && hu<=w) }'; then
  mode=final_window
  target=$(awk -v c="$WEEKLY_FINAL_CEIL" 'BEGIN{ printf "%.2f", c }')
else
  mode=normal
  target=$(awk -v sl="$WEEKLY_SLOPE" -v h="$hours_since" 'BEGIN{ printf "%.2f", sl*h }')
fi

# --- decide --------------------------------------------------------------
w_ok=$(awk -v w="$weekly_pct" -v t="$target" 'BEGIN{ print (w<t)?1:0 }')
f_ok=$(awk -v f="$five_pct" -v c="$FIVE_HOUR_CEIL" 'BEGIN{ print (f<c)?1:0 }')

if [ "$w_ok" = 1 ] && [ "$f_ok" = 1 ]; then
  decision=pass; reason=ok
else
  decision=fail
  if [ "$w_ok" = 0 ] && [ "$f_ok" = 0 ]; then reason=weekly_and_five_over
  elif [ "$w_ok" = 0 ]; then reason=$([ "$mode" = final_window ] && echo weekly_over_final_ceil || echo weekly_over_pace)
  else reason=five_hour_ceiling; fi
fi

echo "weekly_pct=$weekly_pct target_pct=$target mode=$mode five_hour_pct=$five_pct hours_into_week=$hours_since hours_until_reset=$hours_until weekly_reset=$weekly_reset gate_decision=$decision gate_reason=$reason"
[ "$decision" = pass ] && exit 0 || exit 1
