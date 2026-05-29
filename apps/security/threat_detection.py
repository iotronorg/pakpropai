import base64
import json as _json
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ThreatResult:
    detected: bool
    event_type: str = ''
    severity: int = 3
    detail: str = ''
    matched_patterns: list = field(default_factory=list)


_SQL_PATTERNS = [
    re.compile(r"(?i)\b(union\s+select|select\s+.+\s+from|insert\s+into|drop\s+table"
               r"|delete\s+from|update\s+.+\s+set|exec\s*\(|execute\s*\(|xp_cmdshell"
               r"|information_schema|sysobjects|syscolumns)\b"),
    re.compile(r"(?i)(-{2}|\bor\b\s+[\d'\"(]+\s*=\s*[\d'\"]+|\band\b\s+[\d'\"(]+\s*=\s*[\d'\"]+)"),
    re.compile(r"(?i)(;|\bwaitfor\b|\bsleep\b\s*\(|\bbenchmark\b\s*\()"),
    re.compile(r"(?:'|%27)\s*(or|and)\s*(?:'|%27)?[\d]"),
    re.compile(r"(?i)\b(char|nchar|varchar|convert|cast)\s*\("),
    re.compile(r"(?i)\bor\b\s*\(|\band\b\s*\("),
]


def _check_sql_injection(value: str) -> Optional[ThreatResult]:
    matched = [p.pattern for p in _SQL_PATTERNS if p.search(value)]
    if matched:
        return ThreatResult(
            detected=True,
            event_type='sql_injection',
            severity=4,
            detail=f'SQL injection pattern in input (len={len(value)})',
            matched_patterns=matched[:3],
        )
    return None


_TENANT_OVERRIDE_HEADERS = frozenset({
    'HTTP_X_TENANT_ID',
    'HTTP_X_ORG_ID',
    'HTTP_X_ORGANIZATION_ID',
    'HTTP_X_WORKSPACE_ID',
})

_INTERNAL_HEADERS_FORBIDDEN_EXTERNALLY = frozenset({
    'HTTP_X_FORWARDED_USER',
    'HTTP_X_AUTHENTICATED_USER',
    'HTTP_X_REAL_USER_ID',
    'HTTP_X_BYPASS_AUTH',
    'HTTP_X_ADMIN_OVERRIDE',
})


def _check_cross_tenant(request_meta: dict, authenticated_org_id: str) -> Optional[ThreatResult]:
    for header in _TENANT_OVERRIDE_HEADERS:
        supplied = request_meta.get(header, '').strip()
        if not supplied:
            continue
        if supplied != str(authenticated_org_id):
            return ThreatResult(
                detected=True,
                event_type='cross_tenant',
                severity=4,
                detail=f'Tenant override header {header} ({supplied!r}) != auth org ({authenticated_org_id!r})',
            )
    return None


def _check_suspicious_headers(request_meta: dict) -> Optional[ThreatResult]:
    found = [h for h in _INTERNAL_HEADERS_FORBIDDEN_EXTERNALLY if request_meta.get(h)]
    if found:
        return ThreatResult(
            detected=True,
            event_type='suspicious_header',
            severity=4,
            detail=f'Forbidden internal headers present: {found}',
        )
    return None


_MAX_JWT_BYTES = 8192


def _check_jwt_anomaly(auth_header: str) -> Optional[ThreatResult]:
    if not auth_header or not auth_header.lower().startswith('bearer '):
        return None
    token = auth_header[7:].strip()

    if len(token.encode()) > _MAX_JWT_BYTES:
        return ThreatResult(
            detected=True,
            event_type='jwt_anomaly',
            severity=3,
            detail=f'Oversized JWT token ({len(token)} chars)',
        )

    parts = token.split('.')
    if len(parts) != 3:
        return None

    try:
        padded = parts[0] + '=' * (-len(parts[0]) % 4)
        header = _json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return ThreatResult(
            detected=True,
            event_type='jwt_anomaly',
            severity=3,
            detail='JWT header base64 decode failed — possible forgery attempt',
        )

    alg = header.get('alg', '').lower()
    if alg == 'none':
        return ThreatResult(
            detected=True,
            event_type='jwt_anomaly',
            severity=4,
            detail='JWT alg=none algorithm confusion attack detected',
        )

    if alg.startswith('hs') and header.get('kid'):
        return ThreatResult(
            detected=True,
            event_type='jwt_anomaly',
            severity=3,
            detail=f'JWT HS algorithm with kid field — possible key confusion (alg={alg})',
        )

    return None


_PROTECTED_PARAMS = frozenset({
    'org_id', 'organization_id', 'organization', 'user_id', 'actor_id',
    'tenant_id', 'workspace_id', 'owner_id',
})


def _check_parameter_tampering(
    query_params: dict,
    body_data: dict,
    authenticated_user_id: str,
    authenticated_org_id: str,
) -> Optional[ThreatResult]:
    combined = {**query_params, **body_data}
    for param, value in combined.items():
        if param not in _PROTECTED_PARAMS:
            continue
        value_str = str(value)
        if (authenticated_org_id
                and param in ('org_id', 'organization_id', 'organization', 'tenant_id', 'workspace_id')
                and value_str != str(authenticated_org_id)):
            return ThreatResult(
                detected=True,
                event_type='parameter_tampering',
                severity=4,
                detail=f'Param {param!r}={value_str!r} != auth org {authenticated_org_id!r}',
            )
        if (authenticated_user_id
                and param in ('user_id', 'actor_id', 'owner_id')
                and value_str != str(authenticated_user_id)):
            return ThreatResult(
                detected=True,
                event_type='parameter_tampering',
                severity=3,
                detail=f'Param {param!r}={value_str!r} != auth user {authenticated_user_id!r}',
            )
    return None


class ThreatDetectionEngine:

    @staticmethod
    def scan(
        *,
        query_params: dict,
        body_data: dict,
        request_meta: dict,
        auth_header: str,
        authenticated_user_id: str,
        authenticated_org_id: str,
    ) -> Optional[ThreatResult]:
        candidates = []

        for val in list(query_params.values()) + list(body_data.values()):
            if isinstance(val, str):
                r = _check_sql_injection(val)
                if r:
                    candidates.append(r)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        r = _check_sql_injection(item)
                        if r:
                            candidates.append(r)

        r = _check_cross_tenant(request_meta, authenticated_org_id)
        if r:
            candidates.append(r)

        r = _check_suspicious_headers(request_meta)
        if r:
            candidates.append(r)

        r = _check_jwt_anomaly(auth_header)
        if r:
            candidates.append(r)

        r = _check_parameter_tampering(
            query_params, body_data, authenticated_user_id, authenticated_org_id
        )
        if r:
            candidates.append(r)

        if not candidates:
            return None
        return max(candidates, key=lambda x: x.severity)
