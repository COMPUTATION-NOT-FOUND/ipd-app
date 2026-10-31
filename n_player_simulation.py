"""
N-Player Simulation Engine (Phase 3).

Implements game_n() for executing N-player Prisoner's Dilemma matches
with backward compatibility for legacy 3-arg strategies.
"""

import os
import sys
import random
from typing import Callable, Any


# The instruction cap lives in one shared module so every engine uses the same exception class
# and the same tracer (see instruction_limit.py for why the tracer is never switched off per call).
from instruction_limit import (InstructionLimitExceeded, EngineTraceError,  # noqa: F401  (re-exported)
                               MAX_STRATEGY_INSTRUCTIONS, run_with_limit, release_tracer)
from ranking import normalize_ranking, rank_entries, describe_ranking


class InvalidMove(ValueError):
    """A strategy returned something other than exactly 'C' or 'D'."""


# Values whose repr() is plain builtin code. Anything else (e.g. an instance of a class the
# strategy defined) is described by its kind only, so a strategy-defined __repr__ / __eq__ never
# runs outside the instruction cap.
_SAFE_REPR_TYPES = (int, float, bool, type(None), str, bytes)


def validate_move(move):
    """Return ``move`` if it is exactly 'C' or 'D', else raise InvalidMove naming the bad value.

    The single move check shared by 1v1, N-Player and the OS simulation, so a strategy that
    returns 1, None, 'c' or True is rejected the same way everywhere instead of being silently
    counted as cooperation. ``type(move) is str`` (not isinstance) keeps a str subclass with a
    custom __eq__ from deciding the comparison.
    """
    if type(move) is str and move in ('C', 'D'):
        return move
    if type(move) in _SAFE_REPR_TYPES:
        shown = repr(move)
        if len(shown) > 40:
            shown = shown[:37] + '...'
    else:
        shown = 'a non-string value'
    raise InvalidMove(f"returned {shown}; a strategy must return exactly 'C' or 'D'")


def describe_strategy_error(exc):
    """One-line, user-facing reason for a strategy failure (shared by every engine)."""
    if isinstance(exc, InstructionLimitExceeded) or type(exc).__name__ == 'InstructionLimitExceeded':
        return "exceeded the instruction limit (infinite loop?)"
    if isinstance(exc, RecursionError):
        return "exceeded the recursion limit (infinite recursion?)"
    if isinstance(exc, InvalidMove):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def referenced_names(code):
    """Names the code reads anywhere (calls, `key=helper`, ...). A helper is referenced by the
    function that uses it; the real entry point usually isn't referenced at all, so the strategy
    pickers try unreferenced functions first (review, Oct 2026: a 4-arg helper defined above the
    4-arg strategy was picked instead of it). A function calling itself (recursion) doesn't
    count. Empty set if the code doesn't parse."""
    import ast
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, TypeError):
        return set()

    def loads(node):
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    used = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            used |= loads(stmt) - {stmt.name}
        else:
            used |= loads(stmt)
    return used


def call_strategy(
    strategy_func: Callable,
    last_moves: list[str],
    my_history: list[str],
    opponents_histories: list[list[str]],
    meta: dict
) -> str:
    """
    Call a strategy function with signature negotiation.
    
    Supports both legacy 3-arg signatures and new 4-arg N-player signatures:
    - 3-arg: strategy(last_opponent_move, my_history, opponent_history)
    - 4-arg: strategy(last_moves_by_opponent, my_history, opponents_histories, meta)
    
    For 3-arg strategies, provides aggregated opponent view:
    - last_opponent_move = 'D' if any opponent defected, else 'C'
    - opponent_history = list where each round is 'D' if any defected, else 'C'
    
    Args:
        strategy_func: The strategy function to call
        last_moves: List of last moves by opponents (empty on first round)
        my_history: List of my previous moves
        opponents_histories: List of opponent history lists
        meta: Metadata dict (n_players, rng, etc.)
    
    Returns:
        'C' or 'D' move
    """
    def _call_three_arg():
        # Legacy 3-arg view: present opponents as one aggregated opponent.
        # last_opponent_move = 'D' if any opponent defected, else 'C' (None on round 1).
        if last_moves:
            last_opponent_move = 'D' if 'D' in last_moves else 'C'
        else:
            last_opponent_move = None

        # Aggregate opponent history: each round 'D' if any opponent defected, else 'C'.
        opponent_history = []
        if opponents_histories:
            max_len = max(len(h) for h in opponents_histories) if opponents_histories else 0
            for round_idx in range(max_len):
                round_moves = [
                    hist[round_idx] if round_idx < len(hist) else 'C'
                    for hist in opponents_histories
                ]
                opponent_history.append('D' if 'D' in round_moves else 'C')

        return strategy_func(last_opponent_move, my_history, opponent_history)

    # Dispatch by the function's declared positional-parameter count when known, so a
    # 4-arg body that raises its own TypeError isn't mistaken for a 3-arg function.
    code = getattr(strategy_func, '__code__', None)
    n_args = code.co_argcount if code is not None else None

    if n_args == 4:
        return strategy_func(last_moves, my_history, opponents_histories, meta)
    if n_args == 3:
        return _call_three_arg()

    # Unknown signature (builtin, *args, decorated, ...): try 4-arg, then fall back to 3-arg.
    try:
        return strategy_func(last_moves, my_history, opponents_histories, meta)
    except TypeError:
        return _call_three_arg()


def game_n(
    strategies: list[Callable],
    rounds: int,
    payoff_model: Any,
    seed: int | None = None,
    tournament_info: dict | None = None,
    mode: str = "standard",
    discount_factor: float = 0.95,
    stochastic_prob: float = 0.995,
    fixed_random_rounds: int | None = None
) -> dict:
    """
    Execute an N-player Prisoner's Dilemma match.
    
    Args:
        strategies: List of strategy functions (can be 3-arg or 4-arg)
        rounds: Number of rounds to play
        payoff_model: PayoffModel instance for computing payoffs
        seed: Random seed for determinism (optional)
        tournament_info: Tournament context dict (optional)
        mode: Game mode. Supported: "standard", "random", "discounted", "stochastic".
            - "random": plays a fixed number of rounds (100-300) unless fixed_random_rounds is provided
            - "discounted": weights payoffs by discount_factor**round
            - "stochastic": continues with probability stochastic_prob after round 1
    
    Returns:
        Dict with results:
            - players: list of player indices
            - total_points_by_player: dict mapping player index to total points
            - coop_counts_by_player: dict mapping player index to cooperation count
            - defect_counts_by_player: dict mapping player index to defection count
            - tournament_info: tournament context
            - rounds_played: number of rounds completed
            - match_complete: True if match completed normally
    """
    n_players = len(strategies)
    
    # Create RNG instance if seed provided
    rng = random.Random(seed) if seed is not None else random.Random()
    
    # Initialize tournament_info with N-player specific keys (avoid mutation)
    if tournament_info is None:
        tournament_info = {}
    
    # Create new dict to avoid mutating caller's input
    tournament_info = {
        **tournament_info,
        'format': 'n_player',
        'n_players': n_players,
        'payoff_model': describe_payoff_model(payoff_model),
    }
    
    # Each player reads its own copy, so one strategy can't change what the others see.
    import copy as _copy
    player_info = [_copy.deepcopy(tournament_info) for _ in range(n_players)]

    # Initialize tracking structures
    total_points = [0.0] * n_players
    coop_counts = [0] * n_players
    defect_counts = [0] * n_players
    histories = [[] for _ in range(n_players)]
    
    # Determine round limit based on mode
    round_limit = rounds
    if mode == 'random':
        if fixed_random_rounds is not None:
            round_limit = fixed_random_rounds
        else:
            # Deterministic per match when seed is provided
            round_limit = rng.randint(100, 300)

    rounds_played = 0
    # First strategy failure (crash, instruction cap, or a move that isn't 'C'/'D'). The match
    # stops there, like 1v1: the failing player forfeits and the round in progress is discarded.
    error_player = None
    error_reason = None

    # Main game loop
    for round_num in range(round_limit):
        # Probabilistic continuation (stochastic mode)
        # Match app.py behavior: after at least one completed round,
        # stop with probability 1 - stochastic_prob.
        if mode == 'stochastic' and round_num > 0:
            if rng.random() > float(stochastic_prob):
                break

        moves = []
        
        # Get move from each player
        for player_idx in range(n_players):
            # Build opponents' data for this player
            opponents_indices = [i for i in range(n_players) if i != player_idx]
            
            # Last moves by opponents
            if round_num == 0:
                last_moves = []
            else:
                last_moves = [histories[i][-1] for i in opponents_indices]
            
            # My history
            my_history = histories[player_idx].copy()
            
            # Opponents' histories
            opponents_histories = [histories[i].copy() for i in opponents_indices]
            
            # Meta information
            meta = {
                'round': round_num,
                'n_players': n_players,
                'player_index': player_idx,
                'tournament_info': player_info[player_idx],
                'rng': rng  # Pass RNG for deterministic randomness
            }
            
            # Call strategy with instruction limit
            try:
                move = validate_move(run_with_limit(
                    call_strategy,
                    strategies[player_idx],
                    last_moves,
                    my_history,
                    opponents_histories,
                    meta,
                    limit=MAX_STRATEGY_INSTRUCTIONS
                ))
            except EngineTraceError:
                raise   # engine fault: never blame the strategy
            except InstructionLimitExceeded as e:
                error_player, error_reason = player_idx, describe_strategy_error(e)
                break
            except Exception as e:
                error_player, error_reason = player_idx, describe_strategy_error(e)
                break

            moves.append(move)

        if error_player is not None:
            break

        # Update move counts (only for completed rounds, so a discarded round leaves no trace)
        for player_idx, move in enumerate(moves):
            if move == 'C':
                coop_counts[player_idx] += 1
            else:
                defect_counts[player_idx] += 1

        # Compute payoffs for this round using the payoff model
        round_payoffs = payoff_model.compute_round_payoffs(
            moves,
            rng,
            {'round': round_num}
        )
        
        # Apply mode-specific weighting to round payoffs
        if mode == 'discounted':
            weight = float(discount_factor) ** round_num
        else:
            weight = 1.0

        # Update total points
        for player_idx in range(n_players):
            total_points[player_idx] += round_payoffs[player_idx] * weight
        
        # Update histories
        for player_idx in range(n_players):
            histories[player_idx].append(moves[player_idx])

        rounds_played += 1
    
    # Build result dictionary
    result = {
        'players': list(range(n_players)),
        'total_points_by_player': {i: total_points[i] for i in range(n_players)},
        'coop_counts_by_player': {i: coop_counts[i] for i in range(n_players)},
        'defect_counts_by_player': {i: defect_counts[i] for i in range(n_players)},
        'tournament_info': tournament_info,
        'rounds_played': rounds_played,
        'match_complete': error_player is None
    }
    if error_player is not None:
        result['error_player'] = error_player
        result['error'] = error_reason

    return result


def describe_payoff_model(payoff_model):
    """What a strategy may know about how the group is paid (meta['tournament_info']['payoff_model']).

    Public Goods: {'type': 'PublicGoodsPayoff', 'name': 'public_goods', 'b': benefit, 'c': cost,
    'nonlinear': {...}}. Pairwise: {'type': 'PairwiseMatrixPayoff', 'name': 'pairwise_matrix',
    'payoff_matrix': {...}, 'aggregate': 'sum'|'avg'}."""
    info = {'type': type(payoff_model).__name__}
    if hasattr(payoff_model, 'b') and hasattr(payoff_model, 'c'):
        info.update(name='public_goods', b=payoff_model.b, c=payoff_model.c,
                    nonlinear=dict(getattr(payoff_model, 'nonlinear', None) or {'type': 'linear'}))
    elif hasattr(payoff_model, 'payoff_matrix'):
        info.update(name='pairwise_matrix',
                    payoff_matrix={k: list(v) for k, v in payoff_model.payoff_matrix.items()},
                    aggregate=getattr(payoff_model, 'aggregate', 'sum'))
    elif hasattr(payoff_model, 'u_C'):
        info.update(name='kcoop_tensor', u_C=list(payoff_model.u_C), u_D=list(payoff_model.u_D))
    return info


def _with_weights(ranking, weights):
    """Weights passed the old way still count, unless the ranking already names its own."""
    if weights and not (isinstance(ranking, dict) and ranking.get('weights')):
        return {**(ranking if isinstance(ranking, dict) else {}), 'weights': weights}
    return ranking


def _play_group(group_strategies, *, group_idx, active_modes, legacy_mode, modes, seed, rounds,
                payoff_model, weights, discount_factor, stochastic_prob, fixed_random_rounds,
                ranking=None):
    """Play one group in every mode. Returns (per_mode_results, errored) where ``errored`` maps
    a local player index to its failure reason; on a failure the remaining modes are skipped."""
    tournament_info_base = {
        'format': 'n_player',
        'mode': legacy_mode,
        'modes': active_modes,
        'n_players': len(group_strategies),
        'group_index': group_idx,
        # The weighted-score weights of this run (from the ranking; Sprint 1: all points).
        'weights': (ranking or {}).get('weights', weights),
        'ranking': ranking,
        'rounds': rounds,
    }
    per_mode_results = {}
    errored = {}
    for mode_idx, mode_name in enumerate(active_modes):
        match_seed = None
        if seed is not None:
            # modes=None keeps the legacy per-group seed
            match_seed = seed + group_idx * 10000 + (0 if modes is None else mode_idx)
        match_result = game_n(
            strategies=group_strategies,
            rounds=rounds,
            payoff_model=payoff_model,
            seed=match_seed,
            tournament_info={**tournament_info_base, 'mode': mode_name},
            mode=mode_name,
            discount_factor=discount_factor,
            stochastic_prob=stochastic_prob,
            fixed_random_rounds=fixed_random_rounds,
        )
        per_mode_results[mode_name] = match_result
        if match_result.get('error_player') is not None:
            errored[match_result['error_player']] = match_result.get('error', '')
            break
    return per_mode_results, errored


def group_tournament(
    strategies,
    rounds,
    group_size=None,
    seed=None,
    payoff_model=None,
    weights=None,
    mode="standard",
    modes: list[str] | None = None,
    discount_factor: float = 0.95,
    stochastic_prob: float = 0.995,
    fixed_random_rounds: int | None = None,
    ranking=None,
):
    """
    Run N-player tournament with one or more group matches.
    
    Args:
        strategies: List of {name, code, func, ...} dicts
        rounds: Rounds per match
        group_size: Players per match (None = all players in one match)
        seed: Random seed for determinism
        payoff_model: PayoffModel instance or config dict
        weights: Weights for ranking {win_rate, cooperation, points}
          mode: One of {'standard','discounted','stochastic','random'}.
              Note: for mode='random', if fixed_random_rounds is None, a single
              round count is chosen once per tournament and reused across all
              group matches for fairness.
    
    Returns:
        {
            'leaderboard': [...],
            'matches': [...],
            'tournament_info': {...}
        }
    """
    # Create RNG instance for tournament operations (grouping/shuffling)
    rng = random.Random(seed) if seed is not None else random.Random()

    # Defensive validation for multi-mode tournaments
    if modes is not None:
        if isinstance(modes, list) and len(modes) == 0:
            raise ValueError("modes must not be empty")
        if isinstance(modes, list) and len(set(modes)) != len(modes):
            raise ValueError("modes must not contain duplicates")

    # Determine active modes (prefer `modes`, keep `mode` for backward compatibility)
    if modes is not None:
        active_modes = list(modes)
    else:
        active_modes = [mode]

    # Defensive normalization
    active_modes = [m for m in active_modes if m is not None]
    if not active_modes:
        active_modes = ['standard']

    legacy_mode = active_modes[0] if len(active_modes) == 1 else 'multi'

    # In random mode, if no fixed_random_rounds is provided, choose it once per tournament
    # using the tournament seed (not per-match sub-seeds). Mirrors app.py's round_robin_tournament.
    tournament_fixed_random_rounds = fixed_random_rounds
    if 'random' in active_modes and fixed_random_rounds is None:
        tournament_rng = random.Random(seed) if seed is not None else random.Random()
        tournament_fixed_random_rounds = tournament_rng.randint(100, 300)
    
    n_strategies = len(strategies)
    
    # Determine group configuration
    if group_size is None or group_size >= n_strategies:
        # All players in one group
        groups = [list(range(n_strategies))]
    else:
        # Split into groups deterministically
        # Shuffle strategies with RNG, then split into chunks
        indices = list(range(n_strategies))
        rng.shuffle(indices)
        
        # Split into groups of size group_size
        groups = []
        for i in range(0, n_strategies, group_size):
            groups.append(indices[i:i + group_size])
        # A leftover group of one has nobody to play, and would otherwise be credited a win for
        # "beating" an empty field. Fold that player into the previous group instead.
        if len(groups) > 1 and len(groups[-1]) == 1:
            groups[-2].extend(groups.pop())
    
    # Initialize leaderboard tracking for all players
    leaderboard_data = {
        i: {
            'name': strategies[i]['name'],
            'total_points': 0.0,
            'total_moves': 0,
            'cooperates': 0,
            'defects': 0,
            'wins': 0,
            'draws': 0,
            'losses': 0,
            'matches_played': 0,
            'avg_points_per_round_sum': 0.0,
            # Per-mode cooperation normalization (parity with 1v1): sum of each match/mode's
            # cooperation ratio and the count of those matches, so each mode counts equally
            # regardless of how many rounds it ran (random/stochastic length can't sway it).
            'mode_coop_ratio_sum': 0.0,
            'mode_coop_ratio_count': 0,
            # Per-mode breakdown (parity with 1v1) so the results page per-mode tabs render real
            # numbers instead of zeros. Keyed by mode name.
            'mode_points_sum': {m: 0.0 for m in active_modes},
            'mode_matches': {m: 0 for m in active_modes},
            'mode_cooperates': {m: 0 for m in active_modes},
            'mode_defects': {m: 0 for m in active_modes},
            'mode_total_moves': {m: 0 for m in active_modes},
        }
        for i in range(n_strategies)
    }
    
    # Track all matches
    all_matches = []
    
    # Strategies that failed (crash, instruction cap, a move other than 'C'/'D'). Global index ->
    # reason. A failed strategy is DISQUALIFIED: it leaves the leaderboard, and its group is
    # replayed without it (same seed) so the others are not cut short or scored against it.
    disqualified = {}

    # Run matches for each group
    for group_idx, group_indices in enumerate(groups):
        members = list(group_indices)
        while True:
            per_mode_results, errored = _play_group(
                [strategies[i].get('func') or strategies[i].get('code') for i in members],
                group_idx=group_idx, active_modes=active_modes, legacy_mode=legacy_mode,
                modes=modes, seed=seed, rounds=rounds, payoff_model=payoff_model,
                weights=weights, discount_factor=discount_factor,
                stochastic_prob=stochastic_prob,
                fixed_random_rounds=tournament_fixed_random_rounds,
                ranking=normalize_ranking(_with_weights(ranking, weights), 'nplayer'),
            )
            if not errored:
                break
            for local_idx, reason in errored.items():
                disqualified.setdefault(members[local_idx], reason)
            members = [g for j, g in enumerate(members) if j not in errored]
            if len(members) < 2:
                break   # nobody left to play against: the group is void

        if errored or len(members) < 2:
            # Void group (fewer than two strategies survived): nothing is scored.
            all_matches.append({
                'group_index': group_idx,
                'players': [strategies[i]['name'] for i in members],
                'player_indices': members,
                'results': None,
                'void': True,
                'disqualified': [{'name': strategies[i]['name'], 'reason': disqualified[i]}
                                 for i in group_indices if i in disqualified],
            })
            continue

        n_local = len(members)
        points_sum = [0.0] * n_local
        coop_sum = [0] * n_local
        defect_sum = [0] * n_local
        moves_sum = [0] * n_local
        points_per_round_sum = [0.0] * n_local
        # Per-mode cooperation ratio (one entry per mode this match actually played)
        coop_ratio_sum = [0.0] * n_local
        coop_ratio_count = [0] * n_local

        for mode_name in active_modes:
            match_result = per_mode_results[mode_name]
            rounds_played = int(match_result.get('rounds_played', 0) or 0)
            for local_idx in range(n_local):
                pts = float(match_result['total_points_by_player'][local_idx])
                player_coops = int(match_result['coop_counts_by_player'][local_idx])
                player_defects = int(match_result['defect_counts_by_player'][local_idx])
                points_sum[local_idx] += pts
                coop_sum[local_idx] += player_coops
                defect_sum[local_idx] += player_defects
                moves_sum[local_idx] += rounds_played
                if rounds_played > 0:
                    points_per_round_sum[local_idx] += pts / rounds_played
                    # Equal-weight this mode's cooperation ratio (length-independent)
                    coop_ratio_sum[local_idx] += player_coops / rounds_played
                    coop_ratio_count[local_idx] += 1

                # Per-mode breakdown (parity with 1v1's mode_points/mode_stats).
                ld = leaderboard_data[members[local_idx]]
                ld['mode_points_sum'][mode_name] += (pts / rounds_played) if rounds_played > 0 else 0.0
                ld['mode_matches'][mode_name] += 1
                ld['mode_cooperates'][mode_name] += player_coops
                ld['mode_defects'][mode_name] += player_defects
                ld['mode_total_moves'][mode_name] += rounds_played

        denom = float(len(active_modes))
        avg_points = [p / denom for p in points_sum]
        avg_points_per_round = [p / denom for p in points_per_round_sum]

        # Determine winner(s) in this group based on averaged points.
        max_points = max(avg_points, default=0.0)
        eps = 1e-12
        top_players = [i for i in range(n_local) if abs(avg_points[i] - max_points) <= eps]

        # Update leaderboard data for each player in this group
        for local_idx, global_idx in enumerate(members):
            ld = leaderboard_data[global_idx]
            ld['total_points'] += avg_points[local_idx]
            ld['cooperates'] += coop_sum[local_idx]
            ld['defects'] += defect_sum[local_idx]
            ld['total_moves'] += moves_sum[local_idx]
            ld['matches_played'] += 1
            ld['avg_points_per_round_sum'] += avg_points_per_round[local_idx]
            ld['mode_coop_ratio_sum'] += coop_ratio_sum[local_idx]
            ld['mode_coop_ratio_count'] += coop_ratio_count[local_idx]
            if local_idx in top_players:
                if len(top_players) == 1:
                    ld['wins'] += 1
                else:
                    ld['draws'] += 1
            else:
                ld['losses'] += 1

        # Record match details
        if len(active_modes) == 1:
            match_payload = per_mode_results[active_modes[0]]
        else:
            first_mode = active_modes[0]
            match_payload = {
                'players': list(range(n_local)),
                'total_points_by_player': {i: avg_points[i] for i in range(n_local)},
                'coop_counts_by_player': {i: coop_sum[i] for i in range(n_local)},
                'defect_counts_by_player': {i: defect_sum[i] for i in range(n_local)},
                'rounds_played': int(per_mode_results[first_mode].get('rounds_played', 0) or 0),
                'rounds_played_by_mode': {m: r.get('rounds_played', 0) for m, r in per_mode_results.items()},
                'mode_results': per_mode_results,
                'match_complete': all(r.get('match_complete', True) for r in per_mode_results.values()),
            }

        match_record = {
            'group_index': group_idx,
            'players': [strategies[i]['name'] for i in members],
            'player_indices': members,
            'results': match_payload,
        }
        removed = [i for i in group_indices if i in disqualified]
        if removed:
            match_record['disqualified'] = [
                {'name': strategies[i]['name'], 'reason': disqualified[i]} for i in removed
            ]
        all_matches.append(match_record)

    # Calculate percentages and prepare leaderboard
    leaderboard = []
    
    # Determine max possible points per round for normalization
    # This depends on payoff model - use a heuristic
    if hasattr(payoff_model, 'payoff_matrix'):
        # Pairwise matrix model
        max_round_points = max([points[0] for points in payoff_model.payoff_matrix.values()])
        # In N-player with pairwise, max is multiplied by (n-1) for sum aggregate
        if payoff_model.aggregate == 'sum':
            # For approximation, use average group size
            avg_group_size = n_strategies / len(groups)
            max_round_points = max_round_points * (avg_group_size - 1)
    elif hasattr(payoff_model, 'b') and hasattr(payoff_model, 'c'):
        # Public goods model
        # Max is when all cooperate: B(n) - c
        # Approximate with average group size
        avg_group_size = n_strategies / len(groups)
        max_round_points = payoff_model.b * avg_group_size - payoff_model.c
    else:
        # Default fallback
        max_round_points = 5.0
    
    for player_idx in range(n_strategies):
        if player_idx in disqualified:
            continue
        data = leaderboard_data[player_idx]
        
        # Calculate cooperation percentage
        if data['total_moves'] > 0:
            cooperation_percentage = (data['cooperates'] / data['total_moves']) * 100
            if data.get('matches_played', 0) > 0:
                avg_points_per_round = data['avg_points_per_round_sum'] / max(data['matches_played'], 1)
            else:
                avg_points_per_round = data['total_points'] / data['total_moves']
            points_percentage = (avg_points_per_round / max(max_round_points, 0.01)) * 100
        else:
            cooperation_percentage = 0.0
            points_percentage = 0.0
            avg_points_per_round = 0.0
        
        # Normalized cooperation %: equal-weight each mode's cooperation ratio so a longer
        # (random/stochastic) mode doesn't dominate. Parity with 1v1's norm_cooperation_percentage.
        if data.get('mode_coop_ratio_count', 0) > 0:
            norm_cooperation_percentage = (data['mode_coop_ratio_sum'] / data['mode_coop_ratio_count']) * 100
        else:
            norm_cooperation_percentage = cooperation_percentage

        # Clamp percentages to [0, 100]
        cooperation_percentage = max(0.0, min(100.0, cooperation_percentage))
        points_percentage = max(0.0, min(100.0, points_percentage))
        norm_cooperation_percentage = max(0.0, min(100.0, norm_cooperation_percentage))

        # Display counts projected onto a standard volume (matches_played * rounds), mirroring
        # 1v1's normalized_cooperates/normalized_defects so the results table reads consistently.
        standard_volume = max(data.get('matches_played', 0), 0) * rounds
        normalized_cooperates = int(round((norm_cooperation_percentage / 100) * standard_volume))
        normalized_defects = int(round(standard_volume - normalized_cooperates))

        # Per-mode breakdown for the results-page per-mode tabs (parity with 1v1).
        # mode_points[mode] = average points-per-round in that mode; mode_stats carries the
        # cooperate/defect counts and cooperation % so each per-mode tab renders real numbers.
        mode_points = {}
        mode_stats = {}
        for m in active_modes:
            m_matches = data['mode_matches'].get(m, 0)
            m_moves = data['mode_total_moves'].get(m, 0)
            m_coops = data['mode_cooperates'].get(m, 0)
            m_defects = data['mode_defects'].get(m, 0)
            mode_points[m] = (data['mode_points_sum'].get(m, 0.0) / m_matches) if m_matches > 0 else 0.0
            mode_stats[m] = {
                'cooperates': m_coops,
                'defects': m_defects,
                'total_moves': m_moves,
                'cooperation_percentage': (m_coops / m_moves * 100) if m_moves > 0 else 0.0,
            }

        leaderboard.append({
            'name': data['name'],
            'total_points': data['total_points'],
            # Average points per round (comparable between groups that played different lengths).
            'points_per_round': avg_points_per_round,
            'cooperation_percentage': cooperation_percentage,
            'norm_cooperation_percentage': norm_cooperation_percentage,
            'points_percentage': points_percentage,
            'wins': data['wins'],
            'draws': data['draws'],
            'losses': data['losses'],
            'cooperates': data['cooperates'],
            'defects': data['defects'],
            'normalized_cooperates': normalized_cooperates,
            'normalized_defects': normalized_defects,
            'total_moves': data['total_moves'],
            'mode_points': mode_points,
            'mode_stats': mode_stats,
            'submitted_at': strategies[player_idx].get('submitted_at'),
        })

    # Order by the chosen ranking (default: the itinerary's "highest total points wins").
    ranking = normalize_ranking(_with_weights(ranking, weights), 'nplayer')
    leaderboard = rank_entries(leaderboard, ranking, 'nplayer')

    # Every disqualified strategy with why, so the page can list them apart from the ranking.
    runtime_errors = [
        {'name': strategies[i]['name'], 'reason': reason, 'error': reason, 'stage': 'during the run'}
        for i, reason in sorted(disqualified.items())
    ]

    result = {
        'leaderboard': leaderboard,
        'matches': all_matches,
        'runtime_errors': runtime_errors,
        'disqualified': runtime_errors,
        'ranking': ranking,
        'ranking_text': describe_ranking(ranking, 'nplayer'),
        'tournament_info': {
            'format': 'n_player',
            'mode': legacy_mode,
            'modes': active_modes,
            'n_strategies': n_strategies,
            'n_groups': len(groups),
            'group_size': group_size,
            'rounds': rounds,
            'weights': weights,
            'seed': seed,
            'discount_factor': discount_factor,
            'stochastic_prob': stochastic_prob,
            'fixed_random_rounds': tournament_fixed_random_rounds,
        }
    }

    return result


# --- Group elimination (final sprint) -------------------------------------------------------

def _per_round_get(entry, metric):
    """Compare players from different groups fairly: total points depend on how many rounds a
    group happened to play (stochastic / random modes), so use points per round instead."""
    if metric == 'total_points':
        return entry.get('points_per_round')
    if metric == 'earliest_submission':
        return entry.get('submitted_at')
    return entry.get(metric)


def elimination_tournament(
    strategies,
    rounds,
    group_size=None,
    seed=None,
    payoff_model=None,
    weights=None,
    mode="standard",
    modes: list[str] | None = None,
    discount_factor: float = 0.95,
    stochastic_prob: float = 0.995,
    fixed_random_rounds: int | None = None,
    ranking=None,
):
    """N-Player as a group elimination tournament (the rule is in elimination.py).

    Every group is played exactly like a one-group ``group_tournament`` (same scoring, same
    ranking, same disqualification). Then the top of each group plus the best wildcards move
    on, until one final group is left.

    Returns the same shape as ``group_tournament`` (``leaderboard``, ``matches``,
    ``tournament_info``, ...) plus ``rounds``: the bracket, round by round. Leaderboard rows
    carry ``stage_reached`` (last round played) and ``stage_order`` (place among the players
    who went out in that round); those two decide the final order.
    """
    import elimination
    from ranking import ELIMINATION_NOTE

    n = len(strategies)
    spec = normalize_ranking(_with_weights(ranking, weights), 'nplayer')
    rng = random.Random(seed) if seed is not None else random.Random()
    G = group_size if (group_size and group_size >= 2) else max(n, 2)

    # Random mode: one match length for the whole tournament, chosen like group_tournament.
    active_modes = list(modes) if modes else [mode or 'standard']
    fixed_rounds = fixed_random_rounds
    if 'random' in active_modes and fixed_random_rounds is None:
        fixed_rounds = (random.Random(seed) if seed is not None else random.Random()).randint(100, 300)

    def order_across(pids):
        """Players from different groups, best first, scored per round."""
        rows = rank_entries([last_row[p] for p in pids], spec, 'nplayer', get=_per_round_get)
        return [r['_pid'] for r in rows]

    players = list(range(n))
    rng.shuffle(players)        # round 1: seeded random order; later rounds: best first
    last_row = {}               # player -> their row from the last group they played
    round_rows = {}             # (round, player) -> their row in that round (for the bracket)
    record = {i: {'wins': 0, 'draws': 0, 'losses': 0, 'groups_played': 0} for i in range(n)}
    disqualified = {}           # player -> reason
    all_matches = []

    def play_round(round_no, groups):
        ranked_groups = []      # per group: (members, players still in best first)
        for gi, members in enumerate(groups):
            res = group_tournament(
                [strategies[i] for i in members], rounds, group_size=None,
                seed=None if seed is None else seed + round_no * 10_000_000 + gi * 1000,
                payoff_model=payoff_model, weights=weights, mode=mode, modes=modes,
                discount_factor=discount_factor, stochastic_prob=stochastic_prob,
                fixed_random_rounds=fixed_rounds, ranking=spec,
            )
            # Rows only carry names: map them back to players (a repeated name maps in order).
            free = {}
            for i in members:
                free.setdefault(strategies[i]['name'], []).append(i)
            for err in res.get('runtime_errors') or []:
                if free.get(err['name']):
                    disqualified.setdefault(free[err['name']].pop(0), err['reason'])
            order = []
            for row in res['leaderboard']:
                if not free.get(row['name']):
                    continue
                pid = free[row['name']].pop(0)
                row['_pid'] = pid
                order.append(pid)
                last_row[pid] = row
                round_rows[(round_no, pid)] = row
                rec = record[pid]
                for k in ('wins', 'draws', 'losses'):
                    rec[k] += row.get(k, 0)
                rec['groups_played'] += 1
            ranked_groups.append((members, order))
            for m in res.get('matches') or []:
                all_matches.append({**m, 'round': round_no, 'group_index': gi})
        return ranked_groups

    rounds_played, stage_of = elimination.run_bracket(players, G, play_round, order_across)
    bracket = elimination.bracket_json(
        rounds_played, lambda p: strategies[p]['name'], disqualified,
        lambda r, p: {k: round_rows[(r, p)].get(k) for k in
                      ('points_per_round', 'total_points', 'cooperation_percentage')})

    leaderboard = []
    for pid, (stage, pos) in stage_of.items():
        row = {k: v for k, v in last_row[pid].items() if k != '_pid'}
        rec = record[pid]
        # Win rate over the whole bracket: groups topped / groups played.
        row.update(wins=rec['wins'], draws=rec['draws'], losses=rec['losses'],
                   groups_played=rec['groups_played'], stage_reached=stage, stage_order=pos,
                   submitted_at=strategies[pid].get('submitted_at'))
        leaderboard.append(row)
    leaderboard = rank_entries(leaderboard, spec, 'nplayer')

    runtime_errors = [
        {'name': strategies[i]['name'], 'reason': reason, 'error': reason, 'stage': 'during the run'}
        for i, reason in sorted(disqualified.items())
    ]
    return {
        'leaderboard': leaderboard,
        'matches': all_matches,
        'rounds': bracket,
        'runtime_errors': runtime_errors,
        'disqualified': runtime_errors,
        'ranking': spec,
        'ranking_text': ELIMINATION_NOTE + ' ' + describe_ranking(spec, 'nplayer'),
        'tournament_info': {
            'format': 'n_player',
            'style': 'elimination',
            'mode': active_modes[0] if len(active_modes) == 1 else 'multi',
            'modes': active_modes,
            'n_strategies': n,
            'n_groups': len(bracket[0]['groups']) if bracket else 0,
            'group_size': group_size,
            'bracket_rounds': len(bracket),
            'rounds': rounds,
            'weights': weights,
            'seed': seed,
            'discount_factor': discount_factor,
            'stochastic_prob': stochastic_prob,
            'fixed_random_rounds': fixed_rounds,
        },
    }
