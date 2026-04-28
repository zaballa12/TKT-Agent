Repository: {repository_name}
Analysis round: {round_index} of {max_rounds}
Target confidence threshold: {confidence_threshold}

Allowed ticket categories:
{ticket_categories}

Allowed recommended actions:
{recommended_actions}

Allowed confidence levels:
{confidence_levels}

Ticket content:
{ticket}

Repository observations:
{observation_summary}

Loaded repository files:
{file_context}

Instructions:
- Use only the repository evidence provided in this execution.
- If the evidence is insufficient, say so explicitly.
- Return a numeric `confidence_score` from 0 to 100.
- If more repository evidence can improve the answer, return `needs_more_context=true`.
- When `needs_more_context=true`, provide focused `additional_search_queries` and `prioritized_files`.
- Prefer repository-oriented search terms, names of classes, methods, labels, config names, and business terms mentioned by the user or implied by the loaded files.
- If this is the final allowed round, answer with the best evidence available and set `needs_more_context=false`.
- Return valid JSON only.
