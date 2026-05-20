PLAN_LIMITS: dict[str, dict[str, int | None]] = {
    'trial':        {'max_agents': 2,    'max_inventory': 20,     'monthly_wa_tokens': 500},
    'basic':        {'max_agents': 10,   'max_inventory': 200,    'monthly_wa_tokens': 5_000},
    'professional': {'max_agents': 50,   'max_inventory': 2_000,  'monthly_wa_tokens': 50_000},
    'enterprise':   {'max_agents': None, 'max_inventory': None,   'monthly_wa_tokens': None},
}

DIMENSION_KEY_MAP = {
    'agents':    'max_agents',
    'inventory': 'max_inventory',
    'wa_tokens': 'monthly_wa_tokens',
}
