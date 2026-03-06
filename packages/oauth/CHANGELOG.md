## 0.7.0-keycardai-oauth (2026-03-06)


- fix(keycardai-oauth): update test to expect OAuthProtocolError for structured error bodies
- feat(keycardai-oauth)!: detailed error reporting
- BREAKING CHANGE: Token exchange HTTP 4xx errors with structured JSON bodies now raise OAuthProtocolError instead of OAuthHttpError. Callers catching OAuthHttpError for these responses must update to catch OAuthProtocolError.

## 0.6.0-keycardai-oauth (2025-11-17)


- feat(keycardai-oauth): client metadata updates

## 0.5.0-keycardai-oauth (2025-09-22)


- feat(keycardai-oauth): client assertion support
- feat(keycardai-oauth): JWKS type support

## 0.4.1-keycardai-oauth (2025-09-17)


- fix(keycardai-oauth): audience checks

## 0.4.0-keycardai-oauth (2025-09-16)


- feat(keycardai-oauth): multi-zone authentication strategy

## 0.3.0-keycardai-oauth (2025-09-16)


- feat(keycardai-oauth): jwt capabilities

## 0.2.0-keycardai-oauth (2025-09-10)


- feat(keycardai-oauth): remove the impersonation logic

## 0.1.0-keycardai-oauth (2025-09-07)


- feat(keycardai-oauth): initial release
