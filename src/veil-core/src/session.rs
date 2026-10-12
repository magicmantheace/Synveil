// SPDX-License-Identifier: MPL-2.0
//! Deterministic graphical session lifecycle, without a login or IPC endpoint.
//!
//! A verified identity can only be constructed inside this module today. A
//! later, separately reviewed local credential verifier must be the sole
//! authority permitted to create it. Preferences and planner output are not
//! credentials. This state machine does not yet authenticate or start users.

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SessionState {
    Locked,
    Authenticating { attempt: u64 },
    Active { session: u64, uid: u32, gid: u32 },
    CleaningUp { session: u64, uid: u32, gid: u32 },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SessionError {
    InvalidState,
    StaleAttempt,
    PrivilegedIdentity,
    IdentifierExhausted,
    Denied,
}

/// Not constructible by external callers. The future credential verifier
/// must be explicitly trusted before any constructor is introduced.
#[derive(Debug)]
pub struct VerifiedIdentity {
    uid: u32,
    gid: u32,
}

#[derive(Debug)]
pub struct SessionTracker {
    state: SessionState,
    sequence: u64,
}

impl Default for SessionTracker {
    fn default() -> Self {
        Self {
            state: SessionState::Locked,
            sequence: 0,
        }
    }
}

impl SessionTracker {
    #[must_use]
    pub fn state(&self) -> SessionState {
        self.state
    }

    /// Start a *new* attempt. Old attempt identifiers cannot unlock a session.
    pub fn begin_login(&mut self) -> Result<u64, SessionError> {
        if self.state != SessionState::Locked {
            return Err(SessionError::InvalidState);
        }
        let attempt = self
            .sequence
            .checked_add(1)
            .ok_or(SessionError::IdentifierExhausted)?;
        self.sequence = attempt;
        self.state = SessionState::Authenticating { attempt };
        Ok(attempt)
    }

    pub fn deny_login(&mut self, attempt: u64) -> Result<(), SessionError> {
        self.check_attempt(attempt)?;
        self.state = SessionState::Locked;
        Ok(())
    }

    /// A timeout or UI crash during authentication always relocks the seat.
    pub fn expire_login(&mut self, attempt: u64) -> Result<(), SessionError> {
        self.deny_login(attempt)
    }

    /// No verifier is wired in yet; only module-private identities can enter.
    pub fn complete_login(
        &mut self,
        attempt: u64,
        identity: VerifiedIdentity,
    ) -> Result<u64, SessionError> {
        self.check_attempt(attempt)?;
        if identity.uid == 0 || identity.gid == 0 {
            self.state = SessionState::Locked;
            return Err(SessionError::PrivilegedIdentity);
        }
        self.state = SessionState::Active {
            session: attempt,
            uid: identity.uid,
            gid: identity.gid,
        };
        Ok(attempt)
    }

    /// Trusted root-owned credential file is the only production password
    /// authority. The caller must never provide a PHC hash or an identity.
    /// This is not yet exposed over IPC or wired to the graphical shell.
    pub fn complete_local_login(
        &mut self,
        attempt: u64,
        username: &str,
        password: &[u8],
    ) -> Result<u64, SessionError> {
        self.complete_with_verifier(attempt, username, password, crate::credential::verify_root_credential)
    }

    fn complete_with_verifier<F>(
        &mut self,
        attempt: u64,
        username: &str,
        password: &[u8],
        verifier: F,
    ) -> Result<u64, SessionError>
    where
        F: FnOnce(&str, &[u8]) -> bool,
    {
        self.check_attempt(attempt)?;
        if !verifier(username, password) {
            self.state = SessionState::Locked;
            return Err(SessionError::Denied);
        }
        self.complete_login(attempt, VerifiedIdentity { uid: 1000, gid: 1000 })
    }

    pub fn request_logout(&mut self) -> Result<(), SessionError> {
        match self.state {
            SessionState::Active { session, uid, gid } => {
                self.state = SessionState::CleaningUp { session, uid, gid };
                Ok(())
            }
            _ => Err(SessionError::InvalidState),
        }
    }

    /// Losing the display must not silently preserve or reauthorize a user.
    /// Active session resources still require cleanup before another login.
    pub fn display_failed(&mut self) {
        match self.state {
            SessionState::Authenticating { .. } => self.state = SessionState::Locked,
            SessionState::Active { session, uid, gid } => {
                self.state = SessionState::CleaningUp { session, uid, gid };
            }
            SessionState::Locked | SessionState::CleaningUp { .. } => {}
        }
    }

    pub fn cleanup_finished(&mut self, session: u64) -> Result<(), SessionError> {
        match self.state {
            SessionState::CleaningUp {
                session: active, ..
            } if active == session => {
                self.state = SessionState::Locked;
                Ok(())
            }
            _ => Err(SessionError::InvalidState),
        }
    }

    fn check_attempt(&self, attempt: u64) -> Result<(), SessionError> {
        match self.state {
            SessionState::Authenticating { attempt: active } if active == attempt => Ok(()),
            SessionState::Authenticating { .. } => Err(SessionError::StaleAttempt),
            _ => Err(SessionError::InvalidState),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn verified(uid: u32, gid: u32) -> VerifiedIdentity {
        VerifiedIdentity { uid, gid }
    }

    #[test]
    fn fake_verifier_is_not_authority_after_denial_or_crash() {
        let mut tracker = SessionTracker::default();
        let first = tracker.begin_login().unwrap();
        assert_eq!(
            tracker.complete_with_verifier(first, "synveil", b"incorrect", |_, _| false),
            Err(SessionError::Denied)
        );
        assert_eq!(tracker.state(), SessionState::Locked);
        let second = tracker.begin_login().unwrap();
        assert_eq!(
            tracker.complete_with_verifier(first, "synveil", b"valid", |_, _| true),
            Err(SessionError::StaleAttempt)
        );
        assert_eq!(
            tracker.complete_with_verifier(second, "synveil", b"valid", |_, _| true),
            Ok(second)
        );
        tracker.display_failed();
        assert_eq!(tracker.begin_login(), Err(SessionError::InvalidState));
        tracker.cleanup_finished(second).unwrap();
        assert_eq!(tracker.state(), SessionState::Locked);
    }

    #[test]
    fn denied_authentication_relocks_and_replay_cannot_activate() {
        let mut tracker = SessionTracker::default();
        let old = tracker.begin_login().unwrap();
        assert_eq!(tracker.deny_login(old), Ok(()));
        assert_eq!(tracker.state(), SessionState::Locked);
        let new = tracker.begin_login().unwrap();
        assert_ne!(new, old);
        assert_eq!(
            tracker.complete_login(old, verified(1000, 1000)),
            Err(SessionError::StaleAttempt)
        );
        assert_eq!(
            tracker.state(),
            SessionState::Authenticating { attempt: new }
        );
    }

    #[test]
    fn privileged_identity_is_rejected_even_after_verifier_assertion() {
        for (uid, gid) in [(0, 0), (0, 1000), (1000, 0)] {
            let mut tracker = SessionTracker::default();
            let attempt = tracker.begin_login().unwrap();
            assert_eq!(
                tracker.complete_login(attempt, verified(uid, gid)),
                Err(SessionError::PrivilegedIdentity)
            );
            assert_eq!(tracker.state(), SessionState::Locked);
        }
    }

    #[test]
    fn logout_must_clean_resources_before_next_authentication() {
        let mut tracker = SessionTracker::default();
        let attempt = tracker.begin_login().unwrap();
        assert_eq!(
            tracker.complete_login(attempt, verified(1000, 1000)),
            Ok(attempt)
        );
        assert_eq!(tracker.begin_login(), Err(SessionError::InvalidState));
        tracker.request_logout().unwrap();
        assert_eq!(tracker.begin_login(), Err(SessionError::InvalidState));
        assert_eq!(
            tracker.cleanup_finished(attempt + 1),
            Err(SessionError::InvalidState)
        );
        tracker.cleanup_finished(attempt).unwrap();
        assert_eq!(tracker.state(), SessionState::Locked);
        assert!(tracker.begin_login().is_ok());
    }

    #[test]
    fn display_crash_relocks_auth_but_quarantines_active_session() {
        let mut tracker = SessionTracker::default();
        let first = tracker.begin_login().unwrap();
        tracker.display_failed();
        assert_eq!(tracker.state(), SessionState::Locked);
        assert_eq!(
            tracker.complete_login(first, verified(1000, 1000)),
            Err(SessionError::InvalidState)
        );
        let second = tracker.begin_login().unwrap();
        tracker
            .complete_login(second, verified(1000, 1000))
            .unwrap();
        tracker.display_failed();
        assert_eq!(
            tracker.state(),
            SessionState::CleaningUp {
                session: second,
                uid: 1000,
                gid: 1000
            }
        );
        assert_eq!(tracker.begin_login(), Err(SessionError::InvalidState));
        tracker.cleanup_finished(second).unwrap();
        assert_eq!(tracker.state(), SessionState::Locked);
    }

    #[test]
    fn identifier_overflow_fails_closed() {
        let mut tracker = SessionTracker {
            state: SessionState::Locked,
            sequence: u64::MAX,
        };
        assert_eq!(
            tracker.begin_login(),
            Err(SessionError::IdentifierExhausted)
        );
        assert_eq!(tracker.state(), SessionState::Locked);
    }

    #[test]
    fn duplicate_attempts_and_invalid_cleanup_are_refused() {
        let mut tracker = SessionTracker::default();
        let first = tracker.begin_login().unwrap();
        assert_eq!(tracker.begin_login(), Err(SessionError::InvalidState));
        assert_eq!(
            tracker.cleanup_finished(first),
            Err(SessionError::InvalidState)
        );
        assert_eq!(tracker.expire_login(first), Ok(()));
        assert_eq!(tracker.expire_login(first), Err(SessionError::InvalidState));
    }
}
