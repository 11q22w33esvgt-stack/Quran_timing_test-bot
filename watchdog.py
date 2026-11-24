"""مراقب: لو انتهى وقت الصباح ولم يُنشر شيء اليوم، ينبّه المالكة."""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import post
import schedule as sch


def main():
    config = sch.load_config()
    if not config.get("enabled"):
        return
    now = datetime.now(ZoneInfo(config["timezone"]))
    state = post.load_state()
    if state.get("last_posted_date") == now.strftime("%Y-%m-%d"):
        return
    if state["next_index"] >= len(sch.build_schedule(config)):
        return
    net = post.Net(os.environ["TELEGRAM_BOT_TOKEN"])
    net.send_message(os.environ["OWNER_CHAT_ID"],
                     "⚠️ لم يُنشر شيء اليوم حتى الآن. راجعي تبويب Actions في المستودع.", html=False)


if __name__ == "__main__":
    sys.exit(main())
