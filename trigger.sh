#!/bin/zsh
# このMacから毎日14:00にGitHub Actions(daily.yml)を起動する（launchdから呼ばれる）。
# 認証はMacのghログインを使う。送るかどうかの判定はActions側のnotify.pyが行う。
echo "$(date '+%F %T') 起動"
/opt/homebrew/bin/gh workflow run daily.yml -R kumokawa1105-alt/ordernote-schedule --ref main
