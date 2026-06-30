"""
Payoff Model Abstraction (Phase 2).

Provides a unified interface for computing payoffs in both 2-player
and N-player Prisoner's Dilemma games with various payoff structures.
"""

import random
from typing import Protocol, Any


class PayoffModel(Protocol):
    """Protocol for computing payoffs from a round of moves."""
    
    def compute_round_payoffs(
        self, 
        moves: list[str], 
        rng: random.Random, 
        meta: dict
    ) -> list[float]:
        """
        Compute payoffs for all players given their moves.
        
        Args:
            moves: List of 'C' or 'D' for each player
            rng: Seeded random number generator for determinism
            meta: Additional context (round number, etc.)
        
        Returns:
            List of payoff values (floats) for each player
        """
        ...


class PublicGoodsPayoff:
    """
    Public goods game payoff model.
    
    Formula:
        k = number of cooperators in the round
        If player i cooperates: u_i = B(k) - c
        If player i defects: u_i = B(k)
    
    Where B(k) is the benefit function:
        - linear (default): B(k) = b * k
        - power: B(k) = b * k^alpha
    """
    
    def __init__(
        self, 
        b: float, 
        c: float, 
        nonlinear: dict[str, Any] | None = None
    ):
        """
        Initialize public goods payoff model.
        
        Args:
            b: Benefit coefficient per cooperator
            c: Cost of cooperation
            nonlinear: Optional dict with 'type' and parameters
                      e.g., {'type': 'power', 'alpha': 1.5}
        """
        self.b = b
        self.c = c
        self.nonlinear = nonlinear or {'type': 'linear'}
    
    def compute_round_payoffs(
        self, 
        moves: list[str], 
        rng: random.Random, 
        meta: dict
    ) -> list[float]:
        """
        Compute public goods payoffs for all players.
        
        Args:
            moves: List of 'C' or 'D' for each player
            rng: Seeded random number generator (unused but kept for protocol)
            meta: Additional context (unused but kept for protocol)
        
        Returns:
            List of payoff values for each player
        """
        # Count cooperators
        k = sum(1 for move in moves if move == 'C')
        
        # Compute benefit function B(k)
        benefit = self._compute_benefit(k)
        
        # Compute payoffs
        payoffs = []
        for move in moves:
            if move == 'C':
                payoffs.append(benefit - self.c)
            else:  # move == 'D'
                payoffs.append(benefit)
        
        return payoffs
    
    def _compute_benefit(self, k: int) -> float:
        """
        Compute benefit function B(k).
        
        Args:
            k: Number of cooperators
        
        Returns:
            Total benefit value
        """
        benefit_type = self.nonlinear.get('type', 'linear')
        
        if benefit_type == 'linear':
            return self.b * k
        elif benefit_type == 'power':
            alpha = self.nonlinear.get('alpha', 1.0)
            return self.b * (k ** alpha)
        else:
            # Default to linear if unknown type
            return self.b * k


class PairwiseMatrixPayoff:
    """
    Pairwise matrix-based payoff model.
    
    Uses a 2x2 payoff matrix and computes pairwise interactions
    between all players, then aggregates the results.
    
    Matrix format: {'CC': [3,3], 'CD': [0,5], 'DC': [5,0], 'DD': [1,1]}
    """
    
    def __init__(
        self, 
        payoff_matrix: dict[str, list[float]], 
        aggregate: str = 'sum'
    ):
        """
        Initialize pairwise matrix payoff model.
        
        Args:
            payoff_matrix: 2x2 payoff matrix dict
                          Keys: 'CC', 'CD', 'DC', 'DD'
                          Values: [payoff_player0, payoff_player1]
            aggregate: How to aggregate pairwise payoffs ('sum' or 'avg')
        """
        self.payoff_matrix = payoff_matrix
        self.aggregate = aggregate
    
    def compute_round_payoffs(
        self, 
        moves: list[str], 
        rng: random.Random, 
        meta: dict
    ) -> list[float]:
        """
        Compute pairwise matrix payoffs for all players.
        
        Args:
            moves: List of 'C' or 'D' for each player
            rng: Seeded random number generator (unused but kept for protocol)
            meta: Additional context (unused but kept for protocol)
        
        Returns:
            List of payoff values for each player
        """
        n = len(moves)
        
        # Edge case: single player has no opponents
        if n == 1:
            return [0.0]
        
        # Compute pairwise payoffs
        payoffs = [0.0] * n
        
        for i in range(n):
            for j in range(n):
                if i != j:
                    # Get the payoff for player i against player j
                    key = moves[i] + moves[j]  # e.g., 'CC', 'CD', 'DC', 'DD'
                    # First element of the matrix entry is for the row player (player i)
                    payoffs[i] += self.payoff_matrix[key][0]
        
        # Apply aggregation
        if self.aggregate == 'avg' and n > 1:
            payoffs = [p / (n - 1) for p in payoffs]

        return payoffs


class KCooperatorTensorPayoff:
    """
    K-Cooperator Tensor Payoff Model (Phase 5).

    A symmetric, count-based payoff model where payoffs depend only on
    the number of cooperators (k) in the group.

    Formula:
        k = number of cooperators in the round
        If player i cooperates: u_i = u_C[k]
        If player i defects: u_i = u_D[k]

    This model is ideal for OS scheduler simulations where scheduler
    decisions can be mapped from game-theoretic payoffs.
    """

    def __init__(self, u_C: list[float], u_D: list[float]):
        """
        Initialize k-cooperator tensor payoff model.

        Args:
            u_C: Payoff array for cooperators indexed by cooperator count k
                 u_C[k] = payoff when there are k cooperators and you cooperate
            u_D: Payoff array for defectors indexed by cooperator count k
                 u_D[k] = payoff when there are k cooperators and you defect

        Raises:
            ValueError: If arrays have different lengths or are too short
        """
        # Validate array lengths match
        if len(u_C) != len(u_D):
            raise ValueError(
                f"u_C and u_D must have the same length, got {len(u_C)} and {len(u_D)}"
            )

        # Validate minimum length (need at least n+1 entries for n>=1 players)
        if len(u_C) < 2:
            raise ValueError(
                f"u_C and u_D must have at least 2 elements (for at least 1 player), got {len(u_C)}"
            )

        self.u_C = u_C
        self.u_D = u_D

    def compute_round_payoffs(
        self,
        moves: list[str],
        rng: random.Random,
        meta: dict
    ) -> list[float]:
        """
        Compute k-cooperator tensor payoffs for all players.

        Args:
            moves: List of 'C' or 'D' for each player
            rng: Seeded random number generator (unused but kept for protocol)
            meta: Additional context (unused but kept for protocol)

        Returns:
            List of payoff values for each player
        """
        # Count total cooperators
        k = sum(1 for move in moves if move == 'C')

        # Validate array is large enough for this group size
        n = len(moves)
        if len(self.u_C) <= n:
            raise ValueError(
                f"u_C/u_D arrays have {len(self.u_C)} elements but need at least {n + 1} "
                f"for {n} players (k ranges from 0 to {n})"
            )

        # Compute payoffs based on move and cooperator count
        payoffs = []
        for move in moves:
            if move == 'C':
                payoffs.append(self.u_C[k])
            else:  # move == 'D'
                payoffs.append(self.u_D[k])

        return payoffs
