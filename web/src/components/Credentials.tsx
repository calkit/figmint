import type { ContentCredentials } from '../model/types'

/**
 * Display for C2PA Content Credentials.
 *
 * Three things are worth a reader's attention, in this order:
 *
 *  1. **Is it AI-generated?** The whole point of the standard's
 *     `digitalSourceType` field, and the reason a reviewer would look here.
 *  2. **Does the signature verify?** `Invalid` means the file was altered after
 *     signing — that is a real integrity failure and is shown as such.
 *  3. **Who signed it?** Useful, but only after the first two.
 *
 * `Valid` is deliberately not shown as a success state. It means the maths
 * checks out but the signer is not in a trust list, which is exactly what
 * locally-signed development artifacts look like. Presenting that as a green
 * tick would teach people the wrong lesson.
 */

const STATE_LABEL: Record<string, string> = {
  Trusted: 'trusted signature',
  Valid: 'signed, untrusted signer',
  Invalid: 'signature does not verify',
}

const STATE_CLASS: Record<string, string> = {
  Trusted: 'ok',
  Valid: 'unknown',
  Invalid: 'missing',
}

export function CredentialBadges({
  credentials,
}: {
  credentials: ContentCredentials
}) {
  const state = credentials.validationState ?? 'unknown'
  return (
    <div className="cred-badges">
      {credentials.machineGenerated && (
        <span className="badge badge-ai" title={credentials.digitalSourceType}>
          AI
        </span>
      )}
      <span
        className={`badge badge-${STATE_CLASS[state] ?? 'unknown'}`}
        title={credentials.warnings?.join(', ')}
      >
        {STATE_LABEL[state] ?? state}
      </span>
    </div>
  )
}

export function CredentialDetail({
  credentials,
}: {
  credentials: ContentCredentials
}) {
  const agent = credentials.softwareAgent ?? credentials.claimGenerator
  const componentIngredients = (credentials.ingredients ?? []).filter(
    (i) => i.relationship === 'componentOf',
  )

  return (
    <div className="cred-detail">
      {credentials.sourceTypeLabel && (
        <div className={credentials.machineGenerated ? 'cred-ai' : 'muted small'}>
          {credentials.sourceTypeLabel}
        </div>
      )}
      {agent && (
        <div className="muted small">
          made with <code>{agent}</code>
        </div>
      )}
      {credentials.signedBy && (
        <div className="muted small">
          signed by <code>{credentials.signedBy}</code>
          {credentials.signedAt && ` · ${credentials.signedAt.slice(0, 10)}`}
        </div>
      )}
      {componentIngredients.length > 0 && (
        <details className="cred-ingredients">
          <summary className="muted small">
            {componentIngredients.length} component
            {componentIngredients.length === 1 ? '' : 's'} recorded
          </summary>
          <ul>
            {componentIngredients.map((ing, i) => (
              <li key={ing.instanceId ?? i} className="muted small">
                {ing.title ?? ing.instanceId ?? 'untitled'}
                {ing.hasManifest && (
                  <span className="cred-nested" title="Has its own credentials">
                    {' '}
                    ↳ signed
                  </span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
