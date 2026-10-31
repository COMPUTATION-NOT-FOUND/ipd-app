"""Group elimination: how many groups each round has, and who moves on.

The rule (agreed for the final sprint):

* A round splits the players into ``ceil(n / G)`` groups of nearly equal size (sizes differ
  by at most one). G is the group size: N-Player's ``group_size``, or the number of cores in
  the OS simulation.
* The next round has half as many groups, rounded up, so ``ceil(groups / 2) * G`` players
  move on. Example with 65 players and G = 5: 13 groups -> 35 move on (7 groups) -> 20 (4)
  -> 10 (2) -> 5 (1). The last single group is the final.
* When the places don't split evenly over the groups (35 from 13 groups), every group sends
  the same number of guaranteed places (``35 // 13`` = its top 2). The places left over (9)
  are wildcards: the best next-placed players across all groups (the 3rd places), compared
  with a score that means the same thing in every group.

Everything here is plain data in, plain data out, so it is easy to test and reuse.
"""
import math


def group_count(n, group_size, min_group=2):
    """Number of groups for ``n`` players. ``min_group`` = smallest allowed group (N-Player
    needs 2 to have a game; the OS simulation pads groups with filler cores, so it allows 1)."""
    if n <= 0:
        return 0
    g = math.ceil(n / max(1, group_size))
    if min_group > 1:
        g = min(g, max(1, n // min_group))
    return max(1, g)


def group_sizes(n, groups):
    """``groups`` sizes adding up to ``n``, differing by at most one (larger ones first)."""
    if groups <= 0:
        return []
    base, extra = divmod(n, groups)
    return [base + 1 if i < extra else base for i in range(groups)]


def advance_count(n, group_size, min_group=2):
    """How many of ``n`` players move on to the next round (0 when this round is the final)."""
    g = group_count(n, group_size, min_group)
    if g <= 1:
        return 0
    return min(math.ceil(g / 2) * group_size, n - 1)


def plan_rounds(n, group_size, min_group=2):
    """The whole bracket for ``n`` players, assuming nobody is disqualified.

    Returns [{'round', 'players', 'groups', 'sizes', 'advance', 'final'}, ...].
    """
    out = []
    rnd = 1
    while n > 0:
        g = group_count(n, group_size, min_group)
        adv = advance_count(n, group_size, min_group)
        out.append({'round': rnd, 'players': n, 'groups': g, 'sizes': group_sizes(n, g),
                    'advance': adv, 'final': g <= 1})
        if g <= 1:
            break
        n, rnd = adv, rnd + 1
    return out


def snake_split(ordered, sizes):
    """Deal ``ordered`` players (best first) into groups of the given ``sizes`` in snake order
    (1, 2, 3, 3, 2, 1, ...), so the strongest players are spread over the groups."""
    groups = [[] for _ in sizes]
    if not groups:
        return groups
    order = list(range(len(sizes)))
    forward = True
    i = 0
    for player in ordered:
        # Walk the snake, skipping groups that are already full.
        while True:
            seq = order if forward else order[::-1]
            if i >= len(seq):
                forward, i = not forward, 0
                continue
            g = seq[i]
            i += 1
            if len(groups[g]) < sizes[g]:
                groups[g].append(player)
                break
    return groups


def pick_advancers(groups, advance, order_across):
    """Choose who moves on.

    ``groups``: one list per group, each holding that group's player ids best first
    (disqualified players already removed). ``order_across(ids)``: returns the given ids
    ordered best first by a score that is comparable across groups.

    Returns (advanced, wildcards, eliminated): ``advanced`` is ordered best first (used to
    seed the next round), ``wildcards`` is the set that got a leftover place, ``eliminated``
    is ordered best first (used for the final standings).
    """
    n_groups = max(1, len(groups))
    total = sum(len(g) for g in groups)
    advance = max(0, min(advance, total))
    guaranteed = advance // n_groups

    # Everyone listed by their place in their own group, then by the shared score: all
    # 1st places before any 2nd place, and so on. Guaranteed places and wildcards both come
    # from this one order, so the rule is the same for every group.
    by_place = []
    for place in range(max((len(g) for g in groups), default=0)):
        same_place = [g[place] for g in groups if place < len(g)]
        by_place.extend((place, pid) for pid in order_across(same_place))

    advanced = [pid for place, pid in by_place if place < guaranteed]
    rest = [pid for place, pid in by_place if place >= guaranteed]
    free = advance - len(advanced)
    wildcards = rest[:free]
    advanced += wildcards
    eliminated = rest[free:]
    return advanced, set(wildcards), eliminated


def run_bracket(players, group_size, play_round, order_across, min_group=2):
    """Play the whole elimination and return (rounds, stage_of).

    ``players``: ids in round-1 order (shuffle them first). ``play_round(round_no, groups)``
    plays every group of a round and returns, per group, ``(members, order)`` where ``order``
    is the members still in (not disqualified), best first. ``order_across(ids)`` orders ids
    from different groups best first.

    ``rounds``: [{'round', 'players', 'groups': [(members, order)], 'advanced', 'wildcards',
    'final'}]. ``stage_of``: id -> (round reached, place among that round's exits).
    """
    rounds, stage_of = [], {}
    alive = list(players)
    round_no = 0
    while alive:
        round_no += 1
        g = group_count(len(alive), group_size, min_group)
        groups = snake_split(alive, group_sizes(len(alive), g))
        played = play_round(round_no, groups)
        still_in = sum(len(order) for _, order in played)
        target = advance_count(len(alive), group_size, min_group) if g > 1 else 0
        final = g <= 1 or still_in <= 2 or target >= still_in
        if final:
            # The final group (or too few left to split): one order decides the last places.
            standing = played[0][1] if len(played) == 1 else \
                order_across([p for _, order in played for p in order])
            advanced, wildcards, out = [], set(), standing
        else:
            advanced, wildcards, out = pick_advancers([order for _, order in played], target,
                                                      order_across)
        for pos, pid in enumerate(out):
            stage_of[pid] = (round_no, pos)
        rounds.append({'round': round_no, 'players': len(alive), 'groups': played,
                       'advanced': advanced, 'wildcards': wildcards, 'final': final})
        if final:
            break
        alive = advanced
    return rounds, stage_of


def bracket_json(rounds, name_of, disqualified, detail):
    """The bracket as plain JSON for the results page: per round, per group, each player's
    place, whether they moved on (and as a wildcard), plus ``detail(round_no, id)`` numbers
    (that player's numbers in that round). Disqualified members are listed last with their
    reason."""
    out = []
    for rd in rounds:
        moved, wild = set(rd['advanced']), rd['wildcards']
        out.append({
            'round': rd['round'], 'players': rd['players'], 'advance': len(rd['advanced']),
            'final': rd['final'],
            'groups': [{'group_index': gi, 'players': [
                {'name': name_of(pid), 'place': place + 1, **detail(rd['round'], pid),
                 'advanced': pid in moved, 'wildcard': pid in wild}
                for place, pid in enumerate(order)
            ] + [{'name': name_of(i), 'disqualified': True, 'reason': disqualified[i]}
                 for i in members if i in disqualified and i not in order]}
                for gi, (members, order) in enumerate(rd['groups'])],
        })
    return out
