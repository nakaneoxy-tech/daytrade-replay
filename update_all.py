"""登録銘柄（一括更新リスト）と日経平均・ドル円の1分足をまとめて更新する。

使い方:
  python update_all.py            # 一括更新
  python update_all.py 6857 7203  # 指定銘柄を取得してリストに登録
タスクスケジューラ等で平日 16:00 頃に定期実行すると練習可能日が蓄積されていく。
"""
import logging
import sys

from dtr import updater

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        for s in sys.argv[1:]:
            print(updater.fetch_and_register(s))
    print(updater.update_all())
