import sqlite3
import re
from datetime import datetime, timedelta
from collections import Counter

conn = sqlite3.connect('copilot_chats.db')
cursor = conn.cursor()

two_weeks_ago = datetime.now() - timedelta(days=14)
two_weeks_ts = two_weeks_ago.timestamp() * 1000

# Get ALL user messages from ZTS sessions in past 2 weeks
query = '''
SELECT m.content
FROM messages m
JOIN sessions s ON m.session_id = s.session_id
WHERE s.repository_url LIKE '%ZTS%'
  AND m.role = 'user'
  AND CAST(m.timestamp AS REAL) > ?
ORDER BY m.timestamp DESC
'''

cursor.execute(query, (two_weeks_ts,))
rows = cursor.fetchall()

print(f"Total user messages: {len(rows)}\n")

# Define categories and their patterns
categories = {
    "TESTING - Run LocalWithAzureServices": [
        r'localwithazureservices',
        r'localwithazureresources', 
        r'ZTS_TestRunMode',
        r'zts_testrunmode',
        r'localwithmocks',
    ],
    "TESTING - Run integration tests": [
        r'run.*integration\s*test',
        r'integration\s*test.*run',
        r'run.*the.*test',
        r'verify.*test',
        r'unit\s*and\s*integration',
    ],
    "TESTING - Use test doubles not mocks": [
        r'test\s*double',
        r'not\s*mock',
        r'don\'t\s*mock',
        r'similar.*what we do for arg',
    ],
    "CODE STYLE - Use LINQ": [
        r'use\s*linq',
        r'selectmany',
        r'linq\s*chain',
        r'better.*linq',
        r'linqy',
        r'linq\s*select',
    ],
    "CODE STYLE - Inline/simplify": [
        r'\binline\s*(this|it|\w+method)',
        r'can\s*we\s*simplif',
        r'too\s*complicated',
        r'this.*messy',
        r'clunky',
        r'push\s*down',
    ],
    "CODE STYLE - Return new/immutable": [
        r'return.*new.*array',
        r'instead\s*of\s*mutating',
        r'new.*instead',
    ],
    "CODE STYLE - Reduce duplication": [
        r'duplicate\s*logic',
        r'duplication',
        r'is this a redundant',
        r'gratuitous',
    ],
    "CODE STYLE - Follow existing patterns": [
        r'use\s*their\s*pattern',
        r'same\s*pattern\s*as',
        r'follow.*convention',
        r'similar.*what\s*we\s*do',
        r'compare\s*to\s*how',
        r'what\s*do\s*we\s*do.*elsewhere',
    ],
    "CODE STYLE - Case sensitivity/consistency": [
        r'case\s*sensitiv',
        r'tolowerinvariant',
        r'ordinalignorecase',
        r'consistent.*with',
    ],
    "AZURE SDK - Use SDK converters/types": [
        r'imodeldataconverter',
        r'sdk.*converter',
        r'response<t>',
        r'azure\s*sdk.*own',
        r'raw.*sdk.*domain',
    ],
    "AZURE SDK - Check docs": [
        r'#microsoftlearn',
        r'#microsoft-docs',
        r'#deepwiki',
        r'#context7',
        r'#ask-es-chat',
    ],
    "PROCESS - Use ADO failure skill": [
        r'ado\s*fail',
        r'ado\s*failure\s*skill',
        r'diagnos.*build',
    ],
    "PROCESS - Create PR": [
        r'create.*pr\s*on\s*remote',
        r'push.*pr',
        r'create\s*a\s*pr',
    ],
    "PROCESS - SmartMerge": [
        r'smartmerge',
        r'smart\s*merge',
        r'smart\s*pop',
    ],
    "PROCESS - Commit then X": [
        r'commit.*then',
        r'push.*then',
        r'commit\s*first',
    ],
    "PROCESS - Update plan/todo": [
        r'update.*plan',
        r'#todo',
        r'plan.*include',
        r'add.*checkpoint',
    ],
    "API DESIGN - Public surface": [
        r'public\s*surface',
        r'move.*public',
        r'internal\s*only',
        r'public.*facing',
    ],
    "API DESIGN - Strongly typed": [
        r'strongly\s*typed',
        r'use.*record\s*type',
        r'record\s*type.*with',
        r'record\s*with\s*equality',
    ],
    "API DESIGN - Separation of concerns": [
        r'separation.*concerns',
        r'push.*logic.*into',
        r'move.*method.*into',
    ],
    "REVIEW - Critical mode": [
        r'critical\s*mode',
        r'review.*critical',
    ],
    "REVIEW - Twitch mode": [
        r'twitch\s*mode',
    ],
    "CODE STYLE - Use Assert patterns": [
        r'assert\.all',
        r'assert\.equivalent',
        r'xunit.*assert',
        r'fluentassertions',
    ],
    "TESTING - Via command line": [
        r'via\s*cmd',
        r'via\s*cmdline',
        r'command\s*line',
        r'from\s*cli',
        r'use\s*cmd\s*for',
    ],
    "CODE STYLE - Use extension method": [
        r'extension\s*method',
        r'use.*our.*extension',
    ],
    "CODE STYLE - Logging patterns": [
        r'structured\s*log',
        r'log.*pattern',
        r'wrap.*pattern',
    ],
    "CODE STYLE - Throw on error": [
        r'throw\s*on\s*first',
        r'bubble\s*error',
        r'throw.*if.*fail',
    ],
    "PROCESS - Verify before action": [
        r'verify.*before',
        r'build.*rerun',
        r'test.*before.*push',
        r'can\s*you\s*tell\s*me.*before',
    ],
    "PROCESS - Resolve PR comments": [
        r'resolve.*comment',
        r'mark.*resolved',
        r'address.*comment',
        r'won\'t\s*fix',
    ],
}

# Count hits per category
category_hits = Counter()
category_examples = {cat: [] for cat in categories}

for (content,) in rows:
    if not content:
        continue
    content_lower = content.lower()
    for category, patterns in categories.items():
        for pattern in patterns:
            if re.search(pattern, content_lower):
                category_hits[category] += 1
                if len(category_examples[category]) < 3:
                    snippet = content.replace('\n', ' ')[:120]
                    category_examples[category].append(snippet)
                break  # Only count once per category per message

# Print sorted by hit count
print("=" * 60)
print("STEERING GUIDANCE PATTERNS - SORTED BY FREQUENCY")
print("=" * 60)

for category, count in category_hits.most_common():
    print(f"\n## {category}: {count} hits")
    for ex in category_examples[category]:
        print(f"   - \"{ex}...\"" if len(ex) == 120 else f"   - \"{ex}\"")
