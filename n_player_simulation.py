"""
N-Player Simulation Engine (Phase 3).

Implements game_n() for executing N-player Prisoner's Dilemma matches
with backward compatibility for legacy 3-arg strategies.
"""

import sys
import random
from typing import Callable, Any


class InstructionLimitExceeded(Exception):
    """Raised when a strategy executes too many instructions."""
    pass


def run_with_limit(func, *args, limit=10000):
    """Execute a function with a strict instruction (line) limit using closure."""
    instr_count = [limit]  # Use mutable list so trace function can modify it
    
    def trace_instructions(frame, event, arg):
        """Trace function to count lines and enforce limits."""
        if event == 'line':
            instr_count[0] -= 1
            if instr_count[0] <= 0:
                raise InstructionLimitExceeded("Loop limit exceeded")
        return trace_instructions
    
    sys.settrace(trace_instructions)
    try:
        result = func(*args)
        return result
    finally:
        sys.settrace(None)


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
        'payoff_model': {
            'type': type(payoff_model).__name__
        }
    }
    
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
                'tournament_info': tournament_info,
                'rng': rng  # Pass RNG for deterministic randomness
            }
            
            # Call strategy with instruction limit
            try:
                move = run_with_limit(
                    call_strategy,
                    strategies[player_idx],
                    last_moves,
                    my_history,
                    opponents_histories,
                    meta,
                    limit=10000
                )
            except InstructionLimitExceeded:
                # If strategy exceeds limit, default to cooperate
                move = 'C'
            except RecursionError:
                # If infinite recursion, default to cooperate
                move = 'C'
            except Exception:
                # If any other error, default to cooperate
                move = 'C'
            
            # Validate move
            if move not in ['C', 'D']:
                move = 'C'
            
            moves.append(move)
            
            # Update move counts
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
        'match_complete': True
    }
    
    return result


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
    
    # Run matches for each group
    for group_idx, group_indices in enumerate(groups):
        # Extract strategy functions for this group
        group_strategies = [
            strategies[i].get('func') or strategies[i].get('code')
            for i in group_indices
        ]

        # Build base tournament info (strategy-visible)
        tournament_info_base = {
            'format': 'n_player',
            'mode': legacy_mode,
            'modes': active_modes,
            'n_players': len(group_indices),
            'group_index': group_idx,
            'weights': weights,
        }

        # Run one N-player match per mode and aggregate
        per_mode_results: dict[str, dict] = {}
        n_local = len(group_indices)
        points_sum = [0.0] * n_local
        coop_sum = [0] * n_local
        defect_sum = [0] * n_local
        moves_sum = [0] * n_local
        points_per_round_sum = [0.0] * n_local
        # Per-mode cooperation ratio (one entry per mode this match actually played)
        coop_ratio_sum = [0.0] * n_local
        coop_ratio_count = [0] * n_local

        for mode_idx, mode_name in enumerate(active_modes):
            # Generate deterministic sub-seed for this match
            match_seed = None
            if seed is not None:
                if modes is None:
                    # Preserve legacy deterministic behavior
                    match_seed = seed + group_idx * 10000
                else:
                    match_seed = seed + group_idx * 10000 + mode_idx

            match_result = game_n(
                strategies=group_strategies,
                rounds=rounds,
                payoff_model=payoff_model,
                seed=match_seed,
                tournament_info={**tournament_info_base, 'mode': mode_name},
                mode=mode_name,
                discount_factor=discount_factor,
                stochastic_prob=stochastic_prob,
                fixed_random_rounds=tournament_fixed_random_rounds,
            )

            per_mode_results[mode_name] = match_result
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
                gidx = group_indices[local_idx]
                ld = leaderboard_data[gidx]
                ld['mode_points_sum'][mode_name] += (pts / rounds_played) if rounds_played > 0 else 0.0
                ld['mode_matches'][mode_name] += 1
                ld['mode_cooperates'][mode_name] += player_coops
                ld['mode_defects'][mode_name] += player_defects
                ld['mode_total_moves'][mode_name] += rounds_played

        denom = float(len(active_modes))
        avg_points = [p / denom for p in points_sum]
        avg_points_per_round = [p / denom for p in points_per_round_sum]

        # Determine winner(s) in this group based on averaged points
        max_points = max(avg_points) if avg_points else 0.0
        eps = 1e-12
        top_players = [i for i, pts in enumerate(avg_points) if abs(pts - max_points) <= eps]

        # Update leaderboard data for each player in this group
        for local_idx, global_idx in enumerate(group_indices):
            leaderboard_data[global_idx]['total_points'] += avg_points[local_idx]
            leaderboard_data[global_idx]['cooperates'] += coop_sum[local_idx]
            leaderboard_data[global_idx]['defects'] += defect_sum[local_idx]
            leaderboard_data[global_idx]['total_moves'] += moves_sum[local_idx]
            leaderboard_data[global_idx]['matches_played'] += 1
            leaderboard_data[global_idx]['avg_points_per_round_sum'] += avg_points_per_round[local_idx]
            leaderboard_data[global_idx]['mode_coop_ratio_sum'] += coop_ratio_sum[local_idx]
            leaderboard_data[global_idx]['mode_coop_ratio_count'] += coop_ratio_count[local_idx]

            if local_idx in top_players:
                if len(top_players) == 1:
                    leaderboard_data[global_idx]['wins'] += 1
                else:
                    leaderboard_data[global_idx]['draws'] += 1
            else:
                leaderboard_data[global_idx]['losses'] += 1

        # Record match details
        if len(active_modes) == 1:
            match_payload = per_mode_results[active_modes[0]]
        else:
            first_mode = active_modes[0] if active_modes else None
            single_mode_rounds_played = 0
            if first_mode is not None:
                single_mode_rounds_played = int(per_mode_results.get(first_mode, {}).get('rounds_played', 0) or 0)
            match_payload = {
                'players': list(range(n_local)),
                'total_points_by_player': {i: avg_points[i] for i in range(n_local)},
                'coop_counts_by_player': {i: coop_sum[i] for i in range(n_local)},
                'defect_counts_by_player': {i: defect_sum[i] for i in range(n_local)},
                'rounds_played': single_mode_rounds_played,
                'rounds_played_by_mode': {m: r.get('rounds_played', 0) for m, r in per_mode_results.items()},
                'mode_results': per_mode_results,
                'match_complete': True,
            }

        all_matches.append({
            'group_index': group_idx,
            'players': [strategies[i]['name'] for i in group_indices],
            'player_indices': group_indices,
            'results': match_payload,
        })
    
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
        })
    
    # Sort leaderboard by total points descending
    leaderboard.sort(key=lambda x: x['total_points'], reverse=True)
    
    # Build result
    result = {
        'leaderboard': leaderboard,
        'matches': all_matches,
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
