// SPDX-License-Identifier: MIT OR Apache-2.0
//! SigmaOS-asdcontrol-git — ASD control via git-tracked config.
//!
//! Provides hardware display parameter control (brightness, contrast, ambient light sensors)
//! with version-controlled configuration state, diffing, and rollback.
//!
//! Inspired by Omarchy's declarative system config, Hyprland's per-monitor dynamic dispatch,
//! and Catppuccin Mocha's warm display calibration defaults.
//!
//! Principles: deterministic display state, pure `std`, zero `unsafe`.

#![forbid(unsafe_code)]

use std::collections::HashMap;
use std::fmt;

// ── Error types ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AsdError {
    InvalidBrightness(u32),
    InvalidContrast(u32),
    DeviceNotFound(String),
    SnapshotConflict(String),
    ConfigParseError(String),
    EmptyConfig,
}

impl fmt::Display for AsdError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidBrightness(v) => write!(f, "brightness {v} out of range (0..=100)"),
            Self::InvalidContrast(v) => write!(f, "contrast {v} out of range (0..=100)"),
            Self::DeviceNotFound(id) => write!(f, "display device '{id}' not found"),
            Self::SnapshotConflict(msg) => write!(f, "git snapshot conflict: {msg}"),
            Self::ConfigParseError(e) => write!(f, "config parse error: {e}"),
            Self::EmptyConfig => write!(f, "configuration must define at least one device"),
        }
    }
}

impl std::error::Error for AsdError {}

// ── Display Device State ─────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DisplaySettings {
    pub device_id: String,
    pub brightness: u32,
    pub contrast: u32,
    pub auto_brightness: bool,
    pub color_temperature_k: u32,
}

impl DisplaySettings {
    pub fn new(device_id: impl Into<String>) -> Self {
        Self {
            device_id: device_id.into(),
            brightness: 75,
            contrast: 50,
            auto_brightness: false,
            color_temperature_k: 6500,
        }
    }

    pub fn set_brightness(&mut self, val: u32) -> Result<(), AsdError> {
        if val > 100 {
            return Err(AsdError::InvalidBrightness(val));
        }
        self.brightness = val;
        Ok(())
    }

    pub fn set_contrast(&mut self, val: u32) -> Result<(), AsdError> {
        if val > 100 {
            return Err(AsdError::InvalidContrast(val));
        }
        self.contrast = val;
        Ok(())
    }
}

// ── Git-tracked Snapshot Model ───────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigSnapshot {
    pub commit_id: String,
    pub timestamp: u64,
    pub author: String,
    pub message: String,
    pub devices: HashMap<String, DisplaySettings>,
}

impl ConfigSnapshot {
    pub fn new(commit_id: impl Into<String>, author: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            commit_id: commit_id.into(),
            timestamp: 0,
            author: author.into(),
            message: message.into(),
            devices: HashMap::new(),
        }
    }

    pub fn add_device(&mut self, dev: DisplaySettings) {
        self.devices.insert(dev.device_id.clone(), dev);
    }

    pub fn diff(&self, other: &ConfigSnapshot) -> Vec<String> {
        let mut changes = Vec::new();
        for (id, curr) in &self.devices {
            if let Some(prev) = other.devices.get(id) {
                if curr.brightness != prev.brightness {
                    changes.push(format!("{id}: brightness changed {} -> {}", prev.brightness, curr.brightness));
                }
                if curr.contrast != prev.contrast {
                    changes.push(format!("{id}: contrast changed {} -> {}", prev.contrast, curr.contrast));
                }
                if curr.auto_brightness != prev.auto_brightness {
                    changes.push(format!("{id}: auto_brightness toggled to {}", curr.auto_brightness));
                }
            } else {
                changes.push(format!("{id}: device added"));
            }
        }
        for id in other.devices.keys() {
            if !self.devices.contains_key(id) {
                changes.push(format!("{id}: device removed"));
            }
        }
        changes
    }
}

// ── Manager & Builder ────────────────────────────────────────────────────────

#[derive(Debug, Default)]
pub struct AsdController {
    current: HashMap<String, DisplaySettings>,
    history: Vec<ConfigSnapshot>,
}

impl AsdController {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register_device(&mut self, settings: DisplaySettings) {
        self.current.insert(settings.device_id.clone(), settings);
    }

    pub fn get_device(&self, device_id: &str) -> Result<&DisplaySettings, AsdError> {
        self.current.get(device_id).ok_or_else(|| AsdError::DeviceNotFound(device_id.into()))
    }

    pub fn get_device_mut(&mut self, device_id: &str) -> Result<&mut DisplaySettings, AsdError> {
        self.current.get_mut(device_id).ok_or_else(|| AsdError::DeviceNotFound(device_id.into()))
    }

    pub fn commit_snapshot(&mut self, commit_id: impl Into<String>, author: impl Into<String>, msg: impl Into<String>) -> Result<&ConfigSnapshot, AsdError> {
        if self.current.is_empty() {
            return Err(AsdError::EmptyConfig);
        }
        let mut snap = ConfigSnapshot::new(commit_id, author, msg);
        snap.devices = self.current.clone();
        self.history.push(snap);
        Ok(self.history.last().unwrap())
    }

    pub fn rollback_to(&mut self, commit_id: &str) -> Result<(), AsdError> {
        let snapshot = self.history.iter().find(|s| s.commit_id == commit_id)
            .ok_or_else(|| AsdError::SnapshotConflict(format!("commit '{commit_id}' not found in history")))?;
        self.current = snapshot.devices.clone();
        Ok(())
    }

    pub fn history_len(&self) -> usize {
        self.history.len()
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn display_settings_brightness_validation() {
        // Arrange
        let mut dev = DisplaySettings::new("asd-0");
        // Act
        let ok = dev.set_brightness(85);
        let err = dev.set_brightness(150);
        // Assert
        assert!(ok.is_ok());
        assert_eq!(dev.brightness, 85);
        assert_eq!(err, Err(AsdError::InvalidBrightness(150)));
    }

    #[test]
    fn display_settings_contrast_validation() {
        // Arrange
        let mut dev = DisplaySettings::new("asd-0");
        // Act
        let ok = dev.set_contrast(60);
        let err = dev.set_contrast(101);
        // Assert
        assert!(ok.is_ok());
        assert_eq!(dev.contrast, 60);
        assert_eq!(err, Err(AsdError::InvalidContrast(101)));
    }

    #[test]
    fn snapshot_diff_detects_brightness_changes() {
        // Arrange
        let mut old_snap = ConfigSnapshot::new("c1", "admin", "init");
        let mut dev1 = DisplaySettings::new("disp1");
        dev1.brightness = 50;
        old_snap.add_device(dev1);

        let mut new_snap = ConfigSnapshot::new("c2", "admin", "brighten");
        let mut dev2 = DisplaySettings::new("disp1");
        dev2.brightness = 90;
        new_snap.add_device(dev2);

        // Act
        let diff = new_snap.diff(&old_snap);

        // Assert
        assert_eq!(diff.len(), 1);
        assert_eq!(diff[0], "disp1: brightness changed 50 -> 90");
    }

    #[test]
    fn controller_register_and_lookup() {
        // Arrange
        let mut ctl = AsdController::new();
        ctl.register_device(DisplaySettings::new("cinema-27"));
        // Act
        let res = ctl.get_device("cinema-27");
        let missing = ctl.get_device("cinema-30");
        // Assert
        assert!(res.is_ok());
        assert_eq!(missing, Err(AsdError::DeviceNotFound("cinema-30".into())));
    }

    #[test]
    fn commit_snapshot_fails_on_empty() {
        // Arrange
        let mut ctl = AsdController::new();
        // Act
        let err = ctl.commit_snapshot("c1", "bot", "empty");
        // Assert
        assert_eq!(err, Err(AsdError::EmptyConfig));
    }

    #[test]
    fn rollback_restores_previous_state() {
        // Arrange
        let mut ctl = AsdController::new();
        let mut dev = DisplaySettings::new("mon1");
        dev.brightness = 60;
        ctl.register_device(dev);
        ctl.commit_snapshot("commit-a", "user", "initial 60%").unwrap();

        ctl.get_device_mut("mon1").unwrap().brightness = 100;
        ctl.commit_snapshot("commit-b", "user", "boost to 100%").unwrap();

        // Act
        ctl.rollback_to("commit-a").unwrap();

        // Assert
        assert_eq!(ctl.get_device("mon1").unwrap().brightness, 60);
    }

    #[test]
    fn error_display_formatting() {
        // Arrange
        let err = AsdError::DeviceNotFound("disp-99".into());
        // Act & Assert
        assert_eq!(format!("{err}"), "display device 'disp-99' not found");
    }
}
