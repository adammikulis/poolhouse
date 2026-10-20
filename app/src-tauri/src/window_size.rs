//! Fit the native frame to usable desktop space without replacing saved preferences.
use tauri::{PhysicalPosition, PhysicalSize, WebviewWindow};

#[derive(Debug, PartialEq)]
struct Fit {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

fn fit(position: (i32, i32), inner: (u32, u32), outer: (u32, u32),
       area: (i32, i32, u32, u32)) -> Option<Fit> {
    let (left, top, width, height) = area;
    if width == 0 || height == 0 || inner.0 == 0 || inner.1 == 0 { return None; }
    let border_x = outer.0.saturating_sub(inner.0);
    let border_y = outer.1.saturating_sub(inner.1);
    let fitted_width = inner.0.min(width.saturating_sub(border_x).max(1));
    let fitted_height = inner.1.min(height.saturating_sub(border_y).max(1));
    let right = i64::from(left) + i64::from(width.saturating_sub(fitted_width + border_x));
    let bottom = i64::from(top) + i64::from(height.saturating_sub(fitted_height + border_y));
    Some(Fit {
        x: i64::from(position.0).clamp(i64::from(left), right) as i32,
        y: i64::from(position.1).clamp(i64::from(top), bottom) as i32,
        width: fitted_width, height: fitted_height,
    })
}

pub fn constrain(window: &WebviewWindow) -> tauri::Result<()> {
    if window.is_maximized()? || window.is_fullscreen()? { return Ok(()); }
    let monitor = window.current_monitor()?.or(window.primary_monitor()?);
    let Some(monitor) = monitor else { return Ok(()); };
    let area = monitor.work_area();
    let position = window.outer_position()?;
    let inner = window.inner_size()?;
    let outer = window.outer_size()?;
    let Some(fitted) = fit((position.x, position.y), (inner.width, inner.height),
                          (outer.width, outer.height),
                          (area.position.x, area.position.y, area.size.width, area.size.height))
        else { return Ok(()); };
    // Lower the existing minimum only where a small work area requires it.
    let scale = monitor.scale_factor();
    if !scale.is_finite() || scale <= 0.0 { return Ok(()); }
    window.set_min_size(Some(PhysicalSize::new(
        ((760.0 * scale) as u32).min(fitted.width),
        ((640.0 * scale) as u32).min(fitted.height))))?;
    if fitted.width != inner.width || fitted.height != inner.height {
        window.set_size(PhysicalSize::new(fitted.width, fitted.height))?;
    }
    if fitted.x != position.x || fitted.y != position.y {
        window.set_position(PhysicalPosition::new(fitted.x, fitted.y))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn saved_small_frame_is_unchanged() {
        assert_eq!(fit((80, 60), (800, 650), (800, 678), (0, 25, 1920, 1030)),
                   Some(Fit { x: 80, y: 60, width: 800, height: 650 }));
    }
    #[test]
    fn large_default_respects_menu_dock_and_titlebar() {
        assert_eq!(fit((0, 0), (1600, 1000), (1600, 1028), (0, 25, 1440, 830)),
                   Some(Fit { x: 0, y: 25, width: 1440, height: 802 }));
    }
    #[test]
    fn retina_negative_monitor_and_partial_offscreen_recover() {
        assert_eq!(fit((-4000, -50), (1600, 1300), (1600, 1356), (-2880, 50, 2880, 1650)),
                   Some(Fit { x: -2880, y: 50, width: 1600, height: 1300 }));
        assert_eq!(fit((-200, 600), (800, 650), (800, 678), (-2880, 50, 2880, 1650)),
                   Some(Fit { x: -800, y: 600, width: 800, height: 650 }));
    }
    #[test]
    fn removed_monitor_and_small_work_area_recover() {
        assert_eq!(fit((6000, 3000), (1600, 1000), (1610, 1030), (0, 20, 500, 400)),
                   Some(Fit { x: 0, y: 20, width: 490, height: 370 }));
        assert_eq!(fit((0, 0), (800, 650), (800, 678), (0, 0, 0, 0)), None);
    }
}
