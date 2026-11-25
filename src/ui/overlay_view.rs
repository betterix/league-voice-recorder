use iced::widget::{button, canvas, column, container, image, mouse_area, row, slider, text, Space};
use iced::{mouse, Alignment, Background, Border, Color, Element, Length, Point, Rectangle, Renderer, Shadow, Size, Theme, Vector};

use crate::app_state::HudDataDto;
use crate::ui::icons::{icon, IconKind};
use crate::ui::theme::{self, FONT_MONO_BOLD, FONT_UI, FONT_UI_BOLD};
use crate::ui::Message;

/// Smooth GPU-rendered horizontal VU meter bar with rounded pill caps
pub struct VuBarCanvas {
    pub level: f32,
    pub fill_color: Color,
    pub track_color: Color,
}

impl canvas::Program<Message> for VuBarCanvas {
    type State = ();

    fn draw(
        &self,
        _state: &Self::State,
        renderer: &Renderer,
        _theme: &Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<canvas::Geometry> {
        let mut frame = canvas::Frame::new(renderer, bounds.size());
        let radius = (bounds.height * 0.5).min(3.0);
        let track = canvas::Path::rounded_rectangle(Point::ORIGIN, bounds.size(), radius.into());
        frame.fill(&track, self.track_color);

        let pct = self.level.clamp(0.0, 1.0);
        if pct > 0.005 {
            let fill_w = (bounds.width * pct).max(bounds.height.min(bounds.width));
            let fill = canvas::Path::rounded_rectangle(
                Point::ORIGIN,
                Size::new(fill_w, bounds.height),
                radius.into(),
            );
            frame.fill(&fill, self.fill_color);
        }
        vec![frame.into_geometry()]
    }
}

/// 36-bar GradeX harmonic waveform (Recorder) & 52-bar Replay progress needle waveform
/// matching `ui/overlay.html` 1:1
pub struct HudWaveformCanvas {
    pub hud: HudDataDto,
    pub phase: f32,
}

fn scale_audio(peak: f32) -> f32 {
    if peak <= 0.0003 {
        return 0.0;
    }
    let norm = ((peak - 0.0003) / 0.9997).max(0.0);
    norm.powf(0.42).clamp(0.0, 1.0)
}

impl canvas::Program<Message> for HudWaveformCanvas {
    type State = ();

    fn draw(
        &self,
        _state: &Self::State,
        renderer: &Renderer,
        _theme: &Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<canvas::Geometry> {
        let mut frame = canvas::Frame::new(renderer, bounds.size());
        let w = bounds.width;
        let h = bounds.height;

        if self.hud.mode == "REPLAY" {
            let bars = 52usize;
            let step = w / (bars - 1) as f32;
            let progress = if self.hud.total_duration > 0.0 {
                (self.hud.current_time / self.hud.total_duration).clamp(0.0, 1.0) as f32
            } else {
                0.0
            };
            let cursor_x = w * progress;
            let center_y = h * 0.5;
            let color_played = theme::hex(0x3DDCC4);
            let color_upcoming = theme::hex(0x3C4655);

            for i in 0..bars {
                let norm = i as f32 / (bars - 1) as f32;
                let envelope = (norm * std::f32::consts::PI).sin().powf(0.85);
                let profile = (norm * 4.0 * std::f32::consts::PI).sin() * 0.45
                    + (norm * 9.0 * std::f32::consts::PI).sin() * 0.35
                    + (norm * 2.0 * std::f32::consts::PI).cos() * 0.20;
                let bh = (2.0 + profile.abs() * envelope * 5.6).clamp(1.8, 7.5);
                let x = i as f32 * step;
                let color = if x <= cursor_x { color_played } else { color_upcoming };
                frame.fill_rectangle(
                    Point::new((x - 1.1).max(0.0), center_y - bh),
                    Size::new(2.2, bh * 2.0),
                    color,
                );
            }

            let needle_x = cursor_x.clamp(1.0, (w - 2.2).max(1.0));
            frame.fill_rectangle(
                Point::new(needle_x, (center_y - 9.0).max(0.0)),
                Size::new(2.2, 18.0_f32.min(h)),
                Color::WHITE,
            );
        } else {
            // Single-row waveform (top = MIC, bottom = DISCORD) determined by `phase` sign or caller
            let bars = 36usize;
            let step = w / (bars - 1) as f32;
            let center_y = h * 0.5;
            let (level, seed, color) = if self.phase >= 0.0 {
                let eff = if self.hud.is_muted || self.hud.is_discord_muted {
                    0.0
                } else {
                    scale_audio(self.hud.mic_level)
                };
                let col = if self.hud.is_muted || self.hud.is_discord_muted {
                    theme::hex(0xEF4444)
                } else {
                    theme::hex(0x3DDCC4)
                };
                (eff, 0.0_f32, col)
            } else {
                (scale_audio(self.hud.discord_level), 2.5_f32, theme::hex(0x8B93FF))
            };

            let abs_phase = self.phase.abs();
            let eff_level = if level > 0.005 {
                (level.powf(0.45) * 1.65).min(1.0)
            } else {
                0.0
            };
            let p1 = abs_phase + seed;
            let p2 = -abs_phase * 0.9 + seed * 1.6;
            let p3 = abs_phase * 1.4;

            for i in 0..bars {
                let norm = i as f32 / (bars - 1) as f32;
                let mut bar_h = 1.8_f32;
                if eff_level > 0.0 && !self.hud.is_paused {
                    let f1 = norm * 4.2 * std::f32::consts::PI;
                    let f2 = norm * 8.4 * std::f32::consts::PI;
                    let f3 = norm * 12.6 * std::f32::consts::PI;
                    let env_boost = (0.40 + 0.60 * (norm * std::f32::consts::PI).sin().powf(0.6)) * 7.5;
                    let w1 = (f1 + p1).sin();
                    let w2 = (f2 + p2).cos() * 0.45;
                    let w3 = (f3 + p3).sin() * 0.25;
                    let variation = 0.35 + 0.38235 * (w1 + w2 + w3).abs();
                    bar_h = (1.8 + eff_level * env_boost * variation).min(9.2);
                }
                let x = i as f32 * step;
                frame.fill_rectangle(
                    Point::new((x - 1.1).max(0.0), center_y - bar_h),
                    Size::new(2.2, bar_h * 2.0),
                    color,
                );
            }
        }

        vec![frame.into_geometry()]
    }
}

pub fn view_overlay<'a>(hud: &'a HudDataDto, phase: f32) -> Element<'a, Message> {
    let alpha = hud.opacity.clamp(0.3, 1.0);
    let is_replay = hud.mode == "REPLAY";

    let label_color = theme::hex(0x9CA3AF);
    let timer_color = theme::hex(0xF2F5F9);
    let divider_color = theme::hex(0x232B37);

    // Left column (224px wide, matching x=16..240 in 350x76)
    let left_col: Element<'a, Message> = if is_replay {
        let wave = canvas(HudWaveformCanvas {
            hud: hud.clone(),
            phase,
        })
        .width(Length::Fill)
        .height(Length::Fixed(22.0));

        let vol_row = row![
            text("VOLUME")
                .size(10)
                .font(FONT_UI_BOLD)
                .color(label_color)
                .width(Length::Fixed(58.0)),
            slider(0..=200, hud.volume, Message::SetReplayVolume)
                .width(Length::Fill)
                .style(move |_, _| {
                    let active_c = if hud.is_muted {
                        theme::hex(0x64748B)
                    } else {
                        theme::hex(0x3DDCC4)
                    };
                    slider::Style {
                        rail: slider::Rail {
                            backgrounds: (
                                Background::Color(active_c),
                                Background::Color(theme::hex(0x232B37)),
                            ),
                            width: 6.0,
                            border: Border {
                                radius: 3.0.into(),
                                width: 0.0,
                                color: Color::TRANSPARENT,
                            },
                        },
                        handle: slider::Handle {
                            shape: slider::HandleShape::Circle { radius: 6.5 },
                            background: Background::Color(active_c),
                            border_width: 0.0,
                            border_color: Color::TRANSPARENT,
                        },
                    }
                }),
        ]
        .spacing(8)
        .align_y(Alignment::Center);

        column![wave, vol_row]
            .spacing(8)
            .width(Length::Fill)
            .into()
    } else {
        let mic_row = row![
            text("MIC")
                .size(10)
                .font(FONT_UI_BOLD)
                .color(label_color)
                .width(Length::Fixed(58.0)),
            canvas(HudWaveformCanvas {
                hud: hud.clone(),
                phase: phase.abs(),
            })
            .width(Length::Fill)
            .height(Length::Fixed(20.0)),
        ]
        .spacing(8)
        .align_y(Alignment::Center);

        let discord_row = row![
            text("DISCORD")
                .size(10)
                .font(FONT_UI_BOLD)
                .color(label_color)
                .width(Length::Fixed(58.0)),
            canvas(HudWaveformCanvas {
                hud: hud.clone(),
                phase: -(phase.abs() * 1.15).max(0.01),
            })
            .width(Length::Fill)
            .height(Length::Fixed(20.0)),
        ]
        .spacing(8)
        .align_y(Alignment::Center);

        column![mic_row, discord_row]
            .spacing(8)
            .width(Length::Fill)
            .into()
    };

    // Vertical divider at x=250
    let divider = container(Space::new().width(Length::Fixed(1.0)).height(Length::Fixed(56.0)))
        .style(move |_| container::Style {
            background: Some(Background::Color(divider_color)),
            ..Default::default()
        });

    // Right column (status + lock button + 19px bold timer)
    let status_el: Element<'a, Message> = if is_replay {
        let (st_icon, st_txt, st_col) = if hud.is_paused {
            (IconKind::Pause, "PAUSED".to_string(), theme::hex(0xF59E0B))
        } else {
            let lbl = if (hud.speed - 1.0).abs() < 0.05 {
                "REPLAY".to_string()
            } else {
                format!("{:.1}x", hud.speed)
            };
            (IconKind::Play, lbl, theme::hex(0x3DDCC4))
        };
        row![
            icon(st_icon, 9.0, st_col),
            text(st_txt).size(9).font(FONT_UI_BOLD).color(st_col),
        ]
        .spacing(4)
        .align_y(Alignment::Center)
        .into()
    } else {
        let (st_col, st_txt) = if hud.is_paused {
            (theme::hex(0xF59E0B), "PAUSED")
        } else {
            (theme::hex(0xEB6372), "REC")
        };
        row![
            icon(IconKind::RecordDot, 8.0, st_col),
            text(st_txt)
                .size(if hud.is_paused { 9 } else { 10 })
                .font(FONT_UI_BOLD)
                .color(st_col),
        ]
        .spacing(4)
        .align_y(Alignment::Center)
        .into()
    };

    let (lock_bg, lock_bd, lock_fg, lock_kind) = if hud.locked {
        (
            theme::hex(0x261E14),
            theme::hex(0x78350F),
            theme::hex(0xF59E0B),
            IconKind::Lock,
        )
    } else {
        (
            theme::hex(0x131B2E),
            theme::hex(0x2A3547),
            theme::hex(0x38BDF8),
            IconKind::Unlock,
        )
    };

    let lock_btn = button(
        container(icon(lock_kind, 11.0, lock_fg))
            .width(Length::Fixed(20.0))
            .height(Length::Fixed(20.0))
            .center_x(Length::Fill)
            .center_y(Length::Fill),
    )
    .padding(1)
    .on_press(Message::ToggleHudLock)
    .style(move |_, _| button::Style {
        background: Some(Background::Color(lock_bg)),
        border: Border {
            color: lock_bd,
            width: 1.0,
            radius: 11.0.into(),
        },
        ..Default::default()
    });

    let right_col = column![
        row![status_el, Space::new().width(Length::Fill), lock_btn]
            .align_y(Alignment::Center)
            .width(Length::Fill),
        container(
            text(&hud.time_str)
                .size(19)
                .font(FONT_MONO_BOLD)
                .color(timer_color),
        )
        .width(Length::Fill)
        .center_x(Length::Fill),
    ]
    .spacing(4)
    .width(Length::Fixed(86.0));

    let body = container(
        row![left_col, divider, right_col]
            .spacing(10)
            .align_y(Alignment::Center)
            .width(Length::Fill)
            .height(Length::Fill),
    )
    .padding([8, 12])
    .width(Length::Fill)
    .height(Length::Fill)
    .style(move |_| container::Style {
        background: Some(Background::Color(Color::from_rgba(
            14.0 / 255.0,
            17.0 / 255.0,
            22.0 / 255.0,
            alpha,
        ))),
        border: Border {
            color: Color::from_rgba(
                35.0 / 255.0,
                43.0 / 255.0,
                55.0 / 255.0,
                alpha,
            ),
            width: 1.5,
            radius: 10.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(0.0, 0.0, 0.0, 0.5 * alpha),
            offset: Vector::new(0.0, 4.0),
            blur_radius: 18.0,
        },
        snap: true,
        ..Default::default()
    });

    if hud.locked {
        body.into()
    } else {
        mouse_area(body).on_press(Message::DragOverlay).into()
    }
}

pub fn view_toast<'a>(logo_handle: Option<&'a image::Handle>) -> Element<'a, Message> {
    let logo_el: Element<'a, Message> = if let Some(handle) = logo_handle {
        image(handle.clone())
            .width(Length::Fixed(38.0))
            .height(Length::Fixed(38.0))
            .into()
    } else {
        icon(IconKind::Mic, 28.0, theme::hex(0x0AC8B9))
    };

    let card = container(
        row![
            logo_el,
            column![
                text("League Voice Recorder")
                    .size(13)
                    .font(FONT_UI_BOLD)
                    .color(theme::hex(0x0AC8B9)),
                text("Running in background. Click tray icon to restore.")
                    .size(11)
                    .font(FONT_UI)
                    .color(theme::hex(0xE2E8F0)),
            ]
            .spacing(3),
        ]
        .spacing(14)
        .align_y(Alignment::Center),
    )
    .padding([12, 16])
    .width(Length::Fill)
    .height(Length::Fill)
    .style(|_| container::Style {
        background: Some(Background::Color(Color::from_rgba(
            11.0 / 255.0,
            19.0 / 255.0,
            41.0 / 255.0,
            0.96,
        ))),
        border: Border {
            color: theme::hex(0x0AC8B9),
            width: 1.5,
            radius: 10.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(0.0, 0.0, 0.0, 0.7),
            offset: Vector::new(0.0, 6.0),
            blur_radius: 24.0,
        },
        snap: true,
        ..Default::default()
    });

    mouse_area(card).on_press(Message::RestoreFromTray).into()
}
