"""
Deterministic Reference Strategies for Testing
These strategies have completely predictable behavior for regression testing.
"""


def always_cooperate(last_opponent_move, my_history, opponent_history):
    """Always returns 'C' (Cooperate)"""
    return "C"


def always_defect(last_opponent_move, my_history, opponent_history):
    """Always returns 'D' (Defect)"""
    return "D"


def tit_for_tat(last_opponent_move, my_history, opponent_history):
    """
    Tit for Tat: Cooperates on first move, then mirrors opponent's last move.
    This is a classic strategy that promotes cooperation.
    """
    if not opponent_history:
        return "C"  # Start with cooperation
    return opponent_history[-1]  # Mirror opponent's last move


# Code strings for use in tournament (since tournament requires string code)
always_cooperate_code = '''
def always_cooperate(last_opponent_move, my_history, opponent_history):
    """Always returns 'C' (Cooperate)"""
    return "C"
'''

always_defect_code = '''
def always_defect(last_opponent_move, my_history, opponent_history):
    """Always returns 'D' (Defect)"""
    return "D"
'''

tit_for_tat_code = '''
def tit_for_tat(last_opponent_move, my_history, opponent_history):
    """Tit for Tat: Cooperates first, then mirrors opponent"""
    if not opponent_history:
        return "C"
    return opponent_history[-1]
'''
