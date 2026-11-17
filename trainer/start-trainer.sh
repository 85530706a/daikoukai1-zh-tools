#!/bin/sh
set -eu

trainer="$1/trainer.py"
python="$2"
url="http://127.0.0.1:8765"

test -r "$trainer" || { printf '%s\n' "找不到修改器程序：$trainer" >&2; exit 1; }

# Match the Python command and the full installation path, excluding the
# previous launcher's shell, which also contains trainer.py in its command.
for pid in $(/usr/bin/pgrep -f ' -I .*/DHHTrainer/trainer[.]py$'); do
    command=$(/bin/ps -ww -o command= -p "$pid") || continue
    case "$command" in
        *" -I $trainer") ;;
        *) continue ;;
    esac
    /bin/kill -TERM "$pid" || exit 1
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        /bin/kill -0 "$pid" 2>/dev/null || break
        /bin/sleep 0.2
    done
    if /bin/kill -0 "$pid" 2>/dev/null &&
        current_command=$(/bin/ps -ww -o command= -p "$pid") &&
        [ "$current_command" = "$command" ]; then
        /bin/kill -KILL "$pid" || exit 1
        for attempt in 1 2 3 4 5; do
            /bin/kill -0 "$pid" 2>/dev/null || break
            /bin/sleep 0.2
        done
    fi
done

log=$(/usr/bin/mktemp /tmp/dhh-trainer.XXXXXX)
trap '/bin/rm -f "$log"' EXIT
cd "$1"
(
    trap '' HUP
    exec "$python" -I "$trainer"
) </dev/null >"$log" 2>&1 &
pid=$!

for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    /bin/kill -0 "$pid" 2>/dev/null || break
    if /usr/sbin/lsof -a -p "$pid" -iTCP:8765 -sTCP:LISTEN -t >/dev/null 2>&1 &&
        /usr/bin/curl --noproxy '*' -fsS --max-time 1 -o /dev/null "$url/api/ports"; then
        exit 0
    fi
    /bin/sleep 0.25
done

/bin/kill -TERM "$pid" 2>/dev/null || true
/usr/bin/tail -n 8 "$log" >&2
printf '%s\n' "新版后台没有在 $url 启动。" >&2
exit 1
