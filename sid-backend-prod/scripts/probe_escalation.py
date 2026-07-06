"""Quick escalation probe script for red-team execution."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from src.memory.conversation_memory import ConversationMemory
from src.agents.escalation_agent import EscalationAgent
from src.agents.sentiment_agent import SentimentResult
import tempfile

results = []

def run(scenario_id, message, expected_escalate, note=''):
    with tempfile.TemporaryDirectory() as tmp:
        mem = ConversationMemory(Path(tmp) / 'session.json')
        mem.data.update({'customer_name': 'Test User', 'phone': '9876543210'})
        mem.save()
        sentiment = SentimentResult('neutral', 0.5, False, '', 'discovery')
        agent = EscalationAgent(mem)
        r = agent.evaluate(message, sentiment)
        status = 'PASS' if r.escalate == expected_escalate else 'FAIL'
        reason_short = r.reason[:40] if r.reason else ''
        results.append((scenario_id, status, message[:50], expected_escalate, r.escalate, reason_short))

# Human request variants
run('E013', 'get me a human please', True, 'get me a human')
run('E014', 'I want to talk to your manager', True, 'talk to your manager')
run('E015', 'please connect me with someone', True, 'connect me with someone')
run('E016', 'I need human help right now', True, 'I need human help')
run('E_std', 'I want to speak to a human', True, 'standard human request')
run('E_std2', 'connect me to an agent', True, 'connect me to an agent')

# Safety variants
run('E009', 'someone is hurt inside the room', True, 'hurt in room')
run('E010', 'my friend fainted inside the room', True, 'friend fainted')
run('E011', 'there is smoke in the room', True, 'smoke in room')
run('E_safe2', 'there is a fire', True, 'fire')
run('E_safe3', 'someone cannot breathe', True, 'cannot breathe')

# Refund variants
run('E001', 'I need a refund', True, 'need a refund')
run('E003', 'I deserve a refund', True, 'deserve a refund')
run('E_ref2', 'give me my money back', True, 'money back')
run('E_ref3', 'I want compensation', False, 'compensation - not matched')

# Misunderstanding
run('J013', "that's not what i asked", True, 'misunderstanding')
run('J014', 'you keep asking the same thing', True, 'keep asking')
run('J018', 'i already told you', True, 'already told you')

header = "{:<10} {:<6} {:<12} {:<12} {:<50} {}".format(
    "ID", "STATUS", "ESCALATED?", "EXPECTED?", "MESSAGE", "REASON"
)
print(header)
print('-' * 110)
for r in results:
    sid, status, msg, expected, actual, reason = r
    print("{:<10} {:<6} {:<12} {:<12} {:<50} {}".format(
        sid, status, str(actual), str(expected), msg, reason
    ))

fails = [r for r in results if r[1] == 'FAIL']
print("\nTotal: %d, PASS: %d, FAIL: %d" % (len(results), len(results) - len(fails), len(fails)))
