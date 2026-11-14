"""每轮最多 500 条待验收；不以人工标记代替发布事务。"""
REVIEW_BATCH_SIZE = 500


def batch_capacity(pending):
    if pending < 0:
        raise ValueError('待验收数量无效')
    return min(8, max(0, REVIEW_BATCH_SIZE - pending))


def pending_count(db):
    return db.execute("SELECT COUNT(*) FROM motions WHERE dataset='bones-seed' AND state='retargeted_pending_quality'").fetchone()[0]
