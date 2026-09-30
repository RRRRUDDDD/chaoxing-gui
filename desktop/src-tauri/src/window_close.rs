//! What happens when the main window's close button is pressed.
//!
//! With the "ask" preference the host keeps the window open and asks the
//! page. A page that never acknowledges the prompt (still loading, blank or
//! broken) must not trap the user, so an unacknowledged prompt exits after
//! `PROMPT_ACK_TIMEOUT`, and a second close while a prompt is open exits too.

use crate::preferences::CloseAction;
use std::sync::Mutex;
use std::time::Duration;

pub const PROMPT_ACK_TIMEOUT: Duration = Duration::from_secs(3);
pub const PROMPT_EVENT: &str = "close-requested";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CloseDecision {
    /// Let the window close; the app then exits and stops the backend.
    Allow,
    Minimize,
    Hide,
    /// Keep the window and show the prompt with this id.
    Prompt(u64),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PromptState {
    Idle,
    Waiting(u64),
    Shown(u64),
}

pub struct CloseGate {
    state: Mutex<(PromptState, u64)>,
}

impl Default for CloseGate {
    fn default() -> Self {
        CloseGate {
            state: Mutex::new((PromptState::Idle, 0)),
        }
    }
}

impl CloseGate {
    pub fn on_close_requested(&self, action: CloseAction) -> CloseDecision {
        let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let (state, next_id) = &mut *guard;
        match action {
            CloseAction::Minimize => CloseDecision::Minimize,
            CloseAction::Tray => CloseDecision::Hide,
            CloseAction::Exit => {
                *state = PromptState::Idle;
                CloseDecision::Allow
            }
            CloseAction::Ask if *state != PromptState::Idle => {
                // Closing again while asked means "just exit".
                *state = PromptState::Idle;
                CloseDecision::Allow
            }
            CloseAction::Ask => {
                *next_id += 1;
                *state = PromptState::Waiting(*next_id);
                CloseDecision::Prompt(*next_id)
            }
        }
    }

    /// The page displayed prompt `id`; it now waits for the user.
    pub fn acknowledge(&self, id: u64) -> bool {
        let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if guard.0 == PromptState::Waiting(id) {
            guard.0 = PromptState::Shown(id);
            true
        } else {
            false
        }
    }

    /// True when prompt `id` was never acknowledged and the app should exit.
    pub fn expire(&self, id: u64) -> bool {
        let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if guard.0 == PromptState::Waiting(id) {
            guard.0 = PromptState::Idle;
            true
        } else {
            false
        }
    }

    /// The user chose or cancelled; the next close asks again.
    pub fn resolve(&self) {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).0 = PromptState::Idle;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn remembered_actions_do_not_prompt() {
        let gate = CloseGate::default();
        assert_eq!(
            gate.on_close_requested(CloseAction::Minimize),
            CloseDecision::Minimize
        );
        assert_eq!(
            gate.on_close_requested(CloseAction::Tray),
            CloseDecision::Hide
        );
        assert_eq!(
            gate.on_close_requested(CloseAction::Exit),
            CloseDecision::Allow
        );
    }

    #[test]
    fn unacknowledged_prompt_expires_and_acknowledged_one_waits() {
        let gate = CloseGate::default();
        let CloseDecision::Prompt(first) = gate.on_close_requested(CloseAction::Ask) else {
            panic!("expected a prompt");
        };
        assert!(gate.expire(first), "a silent page must not keep the window");
        assert!(
            !gate.acknowledge(first),
            "a late acknowledgement is ignored"
        );

        let CloseDecision::Prompt(second) = gate.on_close_requested(CloseAction::Ask) else {
            panic!("expected a prompt");
        };
        assert_ne!(first, second);
        assert!(!gate.acknowledge(first));
        assert!(gate.acknowledge(second));
        assert!(!gate.expire(second), "the user is choosing");
        gate.resolve();
        assert!(matches!(
            gate.on_close_requested(CloseAction::Ask),
            CloseDecision::Prompt(_)
        ));
    }

    #[test]
    fn closing_again_during_a_prompt_exits() {
        let gate = CloseGate::default();
        let CloseDecision::Prompt(id) = gate.on_close_requested(CloseAction::Ask) else {
            panic!("expected a prompt");
        };
        assert!(gate.acknowledge(id));
        assert_eq!(
            gate.on_close_requested(CloseAction::Ask),
            CloseDecision::Allow
        );
        assert!(!gate.expire(id));
        assert!(matches!(
            gate.on_close_requested(CloseAction::Ask),
            CloseDecision::Prompt(_)
        ));
    }
}
