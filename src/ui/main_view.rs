use std::collections::HashSet;

use iced::widget::{
    button, canvas, checkbox, column, container, image, pick_list, row, scrollable, slider,
    text, text_input, Space,
};
use iced::{Alignment, Background, Color, Element, Length};

use crate::app_state::RecordingItem;
use crate::cloud_sync::CloudRecordingDto;
use crate::ui::icons::{icon, strip_leading_emoji, IconKind};
use crate::ui::overlay_view::VuBarCanvas;
use crate::ui::theme::{
    self, with_alpha, Palette, FONT_MONO, FONT_MONO_BOLD, FONT_UI, FONT_UI_BOLD, FONT_UI_HEAVY,
};
use crate::ui::{App, ConfirmAction, Message, MicOption, Tab, ThemeOption};

#[inline]
fn ui_text<'a>(s: impl text::IntoFragment<'a>) -> text::Text<'a> {
    text(s)
        .font(FONT_UI)
        .shaping(text::Shaping::Advanced)
        .line_height(text::LineHeight::Relative(1.25))
}

#[inline]
fn ui_bold<'a>(s: impl text::IntoFragment<'a>) -> text::Text<'a> {
    text(s)
        .font(FONT_UI_BOLD)
        .shaping(text::Shaping::Advanced)
        .line_height(text::LineHeight::Relative(1.25))
}

#[inline]
fn ui_heavy<'a>(s: impl text::IntoFragment<'a>) -> text::Text<'a> {
    text(s)
        .font(FONT_UI_HEAVY)
        .shaping(text::Shaping::Advanced)
        .line_height(text::LineHeight::Relative(1.2))
}

fn icon_label<'a>(kind: IconKind, label: String, size: f32, color: Color) -> Element<'a, Message> {
    row![
        icon(kind, size, color),
        ui_bold(label).size(size).color(color),
    ]
    .spacing(6)
    .align_y(Alignment::Center)
    .into()
}

#[inline]
fn entrance_eased(elapsed_ms: f32, start_ms: f32, end_ms: f32) -> f32 {
    let t = ((elapsed_ms - start_ms) / (end_ms - start_ms)).clamp(0.0, 1.0);
    1.0 - (1.0 - t).powi(3)
}

fn entrance_slide<'a>(el: Element<'a, Message>, eased: f32) -> Element<'a, Message> {
    if eased >= 0.999 {
        el
    } else {
        let offset = (1.0 - eased) * 10.0;
        column![Space::new().height(Length::Fixed(offset)), el]
            .width(Length::Fill)
            .into()
    }
}

pub fn view_main<'a>(app: &'a App) -> Element<'a, Message> {
    let is_dark = theme::is_dark_theme(&app.config.theme);
    let p = theme::palette(is_dark);

    let elapsed = app.entrance_elapsed_ms;
    let a_hdr = entrance_eased(elapsed, 0.0, 180.0);
    let a_cards = entrance_eased(elapsed, 60.0, 260.0);
    let a_tabs = entrance_eased(elapsed, 130.0, 330.0);
    let a_body = entrance_eased(elapsed, 190.0, 400.0);
    let a_foot = entrance_eased(elapsed, 250.0, 450.0);

    let header = entrance_slide(view_header(app, p, a_hdr), a_hdr);
    let banner = view_update_banner(app, p);
    let action_cards = entrance_slide(view_action_cards(app, p, a_cards), a_cards);
    let tab_bar = entrance_slide(view_tab_bar(app, p, a_tabs), a_tabs);
    let tab_body_inner = container(match app.active_tab {
        Tab::Log => view_log_tab(app, p),
        Tab::Recordings => view_recordings_tab(app, p),
        Tab::Settings => view_settings_tab(app, p),
    })
    .padding([10, 12])
    .width(Length::Fill)
    .height(Length::Fill)
    .style(theme::card_style_alpha(p, a_body));
    let tab_body = if a_body >= 0.999 {
        Element::from(tab_body_inner)
    } else {
        column![
            Space::new().height(Length::Fixed((1.0 - a_body) * 10.0)),
            tab_body_inner
        ]
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
    };
    let footer = entrance_slide(view_footer(app, p, a_foot), a_foot);

    let mut root_col = column![header]
        .spacing(8)
        .padding([12, 14])
        .width(Length::Fill)
        .height(Length::Fill);

    if let Some(b) = banner {
        root_col = root_col.push(b);
    }

    root_col = root_col
        .push(action_cards)
        .push(tab_bar)
        .push(tab_body)
        .push(footer);

    let base = container(root_col)
        .width(Length::Fill)
        .height(Length::Fill)
        .style(theme::app_bg_style(p));

    if let Some(ref m) = app.edit_meta {
        let can_upload = app.can_upload_to_cloud();
        let title_str = format!(
            "{} — {}",
            strip_leading_emoji(&app.tr("recordings.details_title", "Edit Scrim Details")),
            m.filename
        );
        let subtitle_str = app.tr(
            "recordings.details_subtitle",
            "Organize scrim metadata, opponent team (VS), and match notes.",
        );
        let mut footer_btns = row![
            Space::new().width(Length::Fill),
            button(
                ui_bold(app.tr("recordings.cancel", "Cancel"))
                    .size(11)
                    .color(p.text_main),
            )
            .padding([5, 12])
            .on_press(Message::CancelEditMeta)
            .style(theme::surface_btn(p, 6.0)),
            button(
                icon_label(
                    IconKind::Check,
                    strip_leading_emoji(&app.tr("recordings.save_details", "Save Details")).to_string(),
                    11.0,
                    Color::WHITE,
                ),
            )
            .padding([5, 14])
            .on_press(Message::SaveEditMeta)
            .style(theme::solid_btn(
                theme::hex(0x059669),
                theme::hex(0x047857),
                theme::hex(0x10B981),
                Color::WHITE,
                6.0,
            )),
        ]
        .spacing(8);

        if can_upload {
            footer_btns = footer_btns.push(
                button(icon_label(
                    IconKind::Upload,
                    strip_leading_emoji(&app.tr("recordings.save_and_upload", "Save & Upload"))
                        .to_string(),
                    11.0,
                    Color::WHITE,
                ))
                .padding([5, 14])
                .on_press(Message::SaveEditMetaAndUpload)
                .style(theme::solid_btn(
                    p.blue,
                    p.blue_hover,
                    p.blue_border,
                    Color::WHITE,
                    6.0,
                )),
            );
        }

        let modal = container(
            column![
                row![
                    column![
                        row![
                            icon(IconKind::Edit, 14.0, p.teal),
                            ui_bold(title_str).size(15).color(p.text_main),
                        ]
                        .spacing(8)
                        .align_y(Alignment::Center),
                        ui_text(subtitle_str).size(11).color(p.text_muted),
                    ]
                    .spacing(2)
                    .width(Length::Fill),
                    button(icon(IconKind::Close, 13.0, p.text_muted))
                        .padding([4, 6])
                        .on_press(Message::CancelEditMeta)
                        .style(theme::solid_btn(
                            Color::TRANSPARENT,
                            p.btn_surface_hover,
                            Color::TRANSPARENT,
                            p.text_muted,
                            4.0,
                        )),
                ]
                .align_y(Alignment::Start),
                container(Space::new().height(Length::Fixed(1.0)))
                    .width(Length::Fill)
                    .style(move |_| container::Style {
                        background: Some(Background::Color(p.border_inner)),
                        ..Default::default()
                    }),
                row![
                    ui_bold(app.tr("recordings.field_date", "Date Recorded:"))
                        .size(12)
                        .color(p.text_main)
                        .width(Length::Fixed(130.0)),
                    text_input("YYYY-MM-DD", &m.recorded_at)
                        .font(FONT_UI)
                        .size(12)
                        .on_input(Message::EditMetaDate)
                        .style(theme::input_style(p))
                        .padding([6, 8]),
                ]
                .spacing(10)
                .align_y(Alignment::Center),
                row![
                    ui_bold(app.tr("recordings.field_patch", "Game Patch:"))
                        .size(12)
                        .color(p.text_main)
                        .width(Length::Fixed(130.0)),
                    text_input(
                        &app.tr("recordings.field_patch_placeholder", "e.g. 14.23, 26.11"),
                        &m.game_patch
                    )
                    .font(FONT_UI)
                    .size(12)
                    .on_input(Message::EditMetaPatch)
                    .style(theme::input_style(p))
                    .padding([6, 8]),
                ]
                .spacing(10)
                .align_y(Alignment::Center),
                row![
                    ui_bold(app.tr("recordings.field_team", "VS Opponent Team:"))
                        .size(12)
                        .color(p.text_main)
                        .width(Length::Fixed(130.0)),
                    text_input(
                        &app.tr("recordings.field_team_placeholder", "e.g. DEO, FNC, G2"),
                        &m.team_tag
                    )
                    .font(FONT_UI)
                    .size(12)
                    .on_input(Message::EditMetaTeam)
                    .style(theme::input_style(p))
                    .padding([6, 8]),
                ]
                .spacing(10)
                .align_y(Alignment::Center),
                row![
                    ui_bold(app.tr("recordings.field_notes", "Scrim Notes:"))
                        .size(12)
                        .color(p.text_main)
                        .width(Length::Fixed(130.0)),
                    text_input(
                        "Enter match notes, draft thoughts, key timestamps...",
                        &m.notes
                    )
                    .font(FONT_UI)
                    .size(12)
                    .on_input(Message::EditMetaNotes)
                    .style(theme::input_style(p))
                    .padding([6, 8]),
                ]
                .spacing(10)
                .align_y(Alignment::Center),
                container(Space::new().height(Length::Fixed(1.0)))
                    .width(Length::Fill)
                    .style(move |_| container::Style {
                        background: Some(Background::Color(p.border_inner)),
                        ..Default::default()
                    }),
                footer_btns,
            ]
            .spacing(12)
            .width(Length::Fixed(480.0)),
        )
        .padding([18, 20])
        .style(theme::modal_card_style(p));

        return container(modal)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .style(move |_| container::Style {
                background: Some(Background::Color(Color::from_rgba(0.0, 0.0, 0.0, 0.65))),
                ..Default::default()
            })
            .into();
    }

    if let Some((ref action, ref prompt)) = app.confirm_delete {
        let title_txt = match action {
            ConfirmAction::DeleteLocal(_) => {
                strip_leading_emoji(&app.tr("recordings.delete_local", "Delete Local")).to_string()
            }
            ConfirmAction::DeleteRemote(_) => {
                app.tr("recordings.delete_confirm_title", "Confirm Remote Deletion")
            }
        };
        let confirm_modal = container(
            column![
                row![
                    icon(IconKind::Warning, 16.0, theme::hex(0xEF4444)),
                    ui_bold(title_txt).size(15).color(p.text_main),
                    Space::new().width(Length::Fill),
                    button(icon(IconKind::Close, 13.0, p.text_muted))
                        .padding([4, 6])
                        .on_press(Message::CancelDelete)
                        .style(theme::solid_btn(
                            Color::TRANSPARENT,
                            p.btn_surface_hover,
                            Color::TRANSPARENT,
                            p.text_muted,
                            4.0,
                        )),
                ]
                .spacing(8)
                .align_y(Alignment::Center),
                ui_text(prompt).size(12).color(p.text_muted),
                row![
                    Space::new().width(Length::Fill),
                    button(
                        ui_bold(app.tr("recordings.cancel", "Cancel"))
                            .size(11)
                            .color(p.text_main),
                    )
                    .padding([5, 14])
                    .on_press(Message::CancelDelete)
                    .style(theme::surface_btn(p, 6.0)),
                    button(icon_label(
                        IconKind::Trash,
                        "Delete".to_string(),
                        11.0,
                        Color::WHITE,
                    ))
                    .padding([5, 14])
                    .on_press(Message::ConfirmDelete)
                    .style(theme::solid_btn(
                        theme::hex(0xDC2626),
                        theme::hex(0xB91C1C),
                        theme::hex(0xEF4444),
                        Color::WHITE,
                        6.0,
                    )),
                ]
                .spacing(8),
            ]
            .spacing(14)
            .width(Length::Fixed(420.0)),
        )
        .padding([18, 20])
        .style(theme::modal_card_style(p));

        return container(confirm_modal)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .style(move |_| container::Style {
                background: Some(Background::Color(Color::from_rgba(0.0, 0.0, 0.0, 0.65))),
                ..Default::default()
            })
            .into();
    }

    base.into()
}

fn view_header<'a>(app: &'a App, p: Palette, alpha: f32) -> Element<'a, Message> {
    let logo: Element<'a, Message> = if let Some(ref handle) = app.logo_handle {
        image(handle.clone())
            .width(Length::Fixed(42.0))
            .height(Length::Fixed(42.0))
            .into()
    } else {
        icon(IconKind::Mic, 28.0, with_alpha(p.gold, alpha))
    };

    let brand = row![
        logo,
        column![
            ui_heavy(app.tr("app.title", "LEAGUE VOICE RECORDER"))
                .size(17)
                .color(with_alpha(p.gold, alpha)),
            ui_text(app.tr(
                "app.subtitle",
                "Automated Discord Voice Capture & In-Game Replay Audio Sync"
            ))
            .size(11)
            .color(with_alpha(p.text_muted, alpha)),
        ]
        .spacing(2),
    ]
    .spacing(12)
    .align_y(Alignment::Center);

    let discord_lbl = app.tr("health.discord", "Discord:");
    let league_lbl = app.tr("health.league", "League Client:");
    let ready_txt = strip_leading_emoji(&app.tr("health.ready", "Ready")).to_string();
    let running_txt = strip_leading_emoji(&app.tr("health.running", "Running")).to_string();
    let offline_txt = strip_leading_emoji(&app.tr("health.offline", "Offline")).to_string();

    let discord_pill = {
        let (dot_c, txt_c, bg_c, bd_c, label) = if app.health.discord_running {
            (
                p.green,
                if p.is_dark { theme::hex(0xE2E8F0) } else { theme::hex(0x065F46) },
                Color::from_rgba(16.0 / 255.0, 185.0 / 255.0, 129.0 / 255.0, 0.10 * alpha),
                Color::from_rgba(16.0 / 255.0, 185.0 / 255.0, 129.0 / 255.0, 0.30 * alpha),
                format!("{} {}", discord_lbl, ready_txt),
            )
        } else {
            (
                theme::hex(0xEF4444),
                if p.is_dark { theme::hex(0xFCA5A5) } else { theme::hex(0x991B1B) },
                Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.10 * alpha),
                Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.30 * alpha),
                format!("{} {}", discord_lbl, offline_txt),
            )
        };
        container(
            row![
                icon(IconKind::RecordDot, 7.0, with_alpha(dot_c, alpha)),
                ui_bold(label).size(11).color(with_alpha(txt_c, alpha)),
            ]
            .spacing(5)
            .align_y(Alignment::Center),
        )
        .padding([3, 7])
        .style(theme::pill_style_r(bg_c, bd_c, with_alpha(txt_c, alpha), 6.0))
    };

    let league_pill = {
        let (dot_c, txt_c, bg_c, bd_c, label) = if app.health.league_running {
            (
                p.green,
                if p.is_dark { theme::hex(0xE2E8F0) } else { theme::hex(0x065F46) },
                Color::from_rgba(16.0 / 255.0, 185.0 / 255.0, 129.0 / 255.0, 0.10 * alpha),
                Color::from_rgba(16.0 / 255.0, 185.0 / 255.0, 129.0 / 255.0, 0.30 * alpha),
                format!("{} {}", league_lbl, running_txt),
            )
        } else {
            (
                theme::hex(0xEF4444),
                if p.is_dark { theme::hex(0xFCA5A5) } else { theme::hex(0x991B1B) },
                Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.10 * alpha),
                Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.30 * alpha),
                format!("{} {}", league_lbl, offline_txt),
            )
        };
        container(
            row![
                icon(IconKind::RecordDot, 7.0, with_alpha(dot_c, alpha)),
                ui_bold(label).size(11).color(with_alpha(txt_c, alpha)),
            ]
            .spacing(5)
            .align_y(Alignment::Center),
        )
        .padding([3, 7])
        .style(theme::pill_style_r(bg_c, bd_c, with_alpha(txt_c, alpha), 6.0))
    };

    let mut ribbon_row = row![].spacing(6).align_y(Alignment::Center);

    if let Some(ref info) = app.update_info {
        if info.available {
            let ver = if info.latest_version.starts_with('v') {
                info.latest_version.clone()
            } else {
                format!("v{}", info.latest_version)
            };
            let (badge_icon, badge_txt) = if app.update_downloading {
                (IconKind::Clock, "Updating...".to_string())
            } else {
                (IconKind::Rocket, format!("Update {}", ver))
            };
            let mut btn = button(icon_label(badge_icon, badge_txt, 11.0, Color::WHITE))
                .padding([4, 12])
                .style(theme::solid_btn(
                    theme::hex(0x2563EB),
                    theme::hex(0x3B82F6),
                    theme::hex(0x60A5FA),
                    Color::WHITE,
                    12.0,
                ));
            if !app.update_downloading {
                btn = btn.on_press(Message::ApplyUpdate);
            }
            ribbon_row = ribbon_row.push(btn);
        }
    }

    let theme_icon_col = with_alpha(
        if p.is_dark { theme::hex(0xFBBF24) } else { p.text_main },
        alpha,
    );
    let theme_btn = button(icon(
        if p.is_dark { IconKind::Moon } else { IconKind::Sun },
        13.0,
        theme_icon_col,
    ))
    .padding([5, 8])
    .on_press(Message::ToggleTheme)
    .style(theme::solid_btn(
        with_alpha(p.btn_surface, alpha),
        p.btn_surface_hover,
        with_alpha(p.border, alpha),
        theme_icon_col,
        6.0,
    ));

    let refresh_lbl = strip_leading_emoji(&app.tr("health.refresh", "Refresh")).to_string();
    let refresh_btn = button(icon_label(
        IconKind::Refresh,
        refresh_lbl,
        11.0,
        with_alpha(p.text_main, alpha),
    ))
    .padding([4, 8])
    .on_press(Message::RefreshHealth)
    .style(theme::surface_btn(p, 6.0));

    ribbon_row = ribbon_row
        .push(discord_pill)
        .push(league_pill)
        .push(theme_btn)
        .push(refresh_btn);

    let health_ribbon = container(ribbon_row)
        .padding([4, 8])
        .style(theme::inner_card_style(p));

    container(
        row![brand, Space::new().width(Length::Fill), health_ribbon]
            .align_y(Alignment::Center)
            .width(Length::Fill),
    )
    .padding([10, 14])
    .width(Length::Fill)
    .style(theme::card_style_alpha(p, alpha))
    .into()
}

fn view_update_banner<'a>(app: &'a App, p: Palette) -> Option<Element<'a, Message>> {
    let info = app.update_info.as_ref()?;
    if !info.available || (app.update_banner_dismissed && !app.update_downloading) {
        return None;
    }

    let ver = if info.latest_version.starts_with('v') {
        info.latest_version.clone()
    } else {
        format!("v{}", info.latest_version)
    };

    let mut top = row![
        icon(IconKind::Rocket, 14.0, theme::hex(0x60A5FA)),
        ui_bold(format!(
            "New Version Available: {} — An update is ready to install!",
            ver
        ))
        .size(12)
        .color(theme::hex(0xF8FAFC)),
        Space::new().width(Length::Fill),
    ]
    .spacing(10)
    .align_y(Alignment::Center);

    if info.download_url.is_some() {
        let (btn_icon, btn_lbl) = if app.update_downloading {
            (IconKind::Clock, "Downloading...".to_string())
        } else {
            (IconKind::Download, "Download & Install Update".to_string())
        };
        let mut dl_btn = button(icon_label(btn_icon, btn_lbl, 11.0, Color::WHITE))
            .padding([4, 14])
            .style(theme::solid_btn(
                theme::hex(0x2563EB),
                theme::hex(0x3B82F6),
                theme::hex(0x60A5FA),
                Color::WHITE,
                6.0,
            ));
        if !app.update_downloading {
            dl_btn = dl_btn.on_press(Message::ApplyUpdate);
        }
        top = top.push(dl_btn);
    }

    if !app.update_downloading {
        top = top.push(
            button(icon(IconKind::Close, 12.0, p.text_muted))
                .padding([4, 8])
                .on_press(Message::DismissUpdateBanner)
                .style(theme::solid_btn(
                    Color::TRANSPARENT,
                    Color::from_rgba(1.0, 1.0, 1.0, 0.1),
                    Color::TRANSPARENT,
                    p.text_muted,
                    4.0,
                )),
        );
    }

    let mut col = column![top].spacing(8);

    if app.update_downloading || app.update_progress.is_some() {
        col = col.push(view_update_progress_box(app, p));
    }

    Some(
        container(col)
            .padding([10, 14])
            .width(Length::Fill)
            .style(move |_| container::Style {
                background: Some(Background::Color(Color::from_rgba(
                    30.0 / 255.0,
                    58.0 / 255.0,
                    138.0 / 255.0,
                    0.95,
                ))),
                border: iced::Border {
                    color: theme::hex(0x3B82F6),
                    width: 1.5,
                    radius: 8.0.into(),
                },
                ..Default::default()
            })
            .into(),
    )
}

fn view_update_progress_box<'a>(app: &'a App, p: Palette) -> Element<'a, Message> {
    let (pct, bar_txt, status_txt, bytes_txt) = if let Some(ref prog) = app.update_progress {
        if prog.status == "installing" {
            (
                1.0_f32,
                "Installing... (100%)".to_string(),
                "Installing update & restarting application...".to_string(),
                "Finalizing...".to_string(),
            )
        } else {
            let pct_f = (prog.percentage as f32 / 100.0).clamp(0.0, 1.0);
            let (label, bytes) = if prog.total > 0 {
                let dl_mb = prog.downloaded as f64 / (1024.0 * 1024.0);
                let tot_mb = prog.total as f64 / (1024.0 * 1024.0);
                (
                    format!("{:.1} MB / {:.1} MB ({}%)", dl_mb, tot_mb, prog.percentage),
                    format!("{:.1} MB / {:.1} MB", dl_mb, tot_mb),
                )
            } else {
                (format!("{}%", prog.percentage), "Downloading...".to_string())
            };
            (
                pct_f,
                label,
                format!("Downloading update... {}%", prog.percentage),
                bytes,
            )
        }
    } else {
        (
            0.0,
            "Connecting...".to_string(),
            "Starting download...".to_string(),
            "Connecting...".to_string(),
        )
    };

    let bar = canvas(VuBarCanvas {
        level: pct,
        fill_color: theme::hex(0x3B82F6),
        track_color: theme::hex(0x1E293B),
    })
    .width(Length::Fill)
    .height(Length::Fixed(18.0));

    container(
        column![
            row![
                ui_bold(status_txt).size(11).color(theme::hex(0xE2E8F0)),
                Space::new().width(Length::Fill),
                ui_bold(bytes_txt).size(11).color(theme::hex(0xE2E8F0)),
                ui_bold(bar_txt).size(11).color(p.gold_light),
            ]
            .spacing(10),
            bar,
        ]
        .spacing(6),
    )
    .padding([10, 14])
    .width(Length::Fill)
    .style(theme::inner_card_style(p))
    .into()
}

fn view_action_cards<'a>(app: &'a App, p: Palette, alpha: f32) -> Element<'a, Message> {
    let rec_active = app.state.rec_active.load(std::sync::atomic::Ordering::SeqCst);
    let rep_active = app.state.rep_active.load(std::sync::atomic::Ordering::SeqCst);
    let rec_stat = app.state.rec_status.lock().map(|g| g.clone()).unwrap_or_default();
    let rep_stat = app.state.rep_status.lock().map(|g| g.clone()).unwrap_or_default();
    let clock = app.state.match_time.lock().map(|g| g.clone()).unwrap_or(None);
    let pulse = (app.wave_phase.sin() * 0.5 + 0.5).clamp(0.0, 1.0);

    // Card 1: Scrim Recorder
    let (rec_icon, rec_pill_txt, rec_pill_c, rec_pill_bg, rec_pill_border) = if rec_stat.0 == "RECORDING" {
        (
            IconKind::RecordDot,
            format!("RECORDING: {}", rec_stat.1),
            Color::from_rgb(0.96, 0.25 + 0.15 * pulse, 0.37 + 0.10 * pulse),
            Color::from_rgba(244.0 / 255.0, 63.0 / 255.0, 94.0 / 255.0, (0.10 + 0.14 * pulse) * alpha),
            Color::from_rgba(244.0 / 255.0, 63.0 / 255.0, 94.0 / 255.0, (0.35 + 0.45 * pulse) * alpha),
        )
    } else if rec_stat.0 == "PAUSED" {
        (
            IconKind::Pause,
            format!("PAUSED: {}", rec_stat.1),
            theme::hex(0xFBBF24),
            Color::from_rgba(251.0 / 255.0, 191.0 / 255.0, 36.0 / 255.0, 0.12 * alpha),
            Color::from_rgba(251.0 / 255.0, 191.0 / 255.0, 36.0 / 255.0, 0.45 * alpha),
        )
    } else if rec_stat.0 == "SAVING" {
        (
            IconKind::Music,
            format!("SAVING: {}", if rec_stat.1.is_empty() { "Finalizing audio..." } else { &rec_stat.1 }),
            theme::hex(0xC084FC),
            Color::from_rgba(192.0 / 255.0, 132.0 / 255.0, 252.0 / 255.0, 0.12 * alpha),
            Color::from_rgba(192.0 / 255.0, 132.0 / 255.0, 252.0 / 255.0, 0.45 * alpha),
        )
    } else if rec_active {
        (
            IconKind::Clock,
            format!("LISTENING: {}", if rec_stat.1.is_empty() { "Waiting for match..." } else { &rec_stat.1 }),
            p.teal,
            Color::from_rgba(10.0 / 255.0, 200.0 / 255.0, 185.0 / 255.0, 0.12 * alpha),
            Color::from_rgba(10.0 / 255.0, 200.0 / 255.0, 185.0 / 255.0, 0.45 * alpha),
        )
    } else {
        (
            IconKind::RecordDot,
            strip_leading_emoji(&app.tr("record.status_idle", "IDLE (Click Start to monitor)")).to_string(),
            p.text_muted,
            with_alpha(p.bg_card_inner, alpha),
            with_alpha(p.border_inner, alpha),
        )
    };

    let rec_btn = if rec_active {
        button(
            container(icon_label(
                IconKind::Stop,
                strip_leading_emoji(&app.tr("record.stop_btn", "Stop Recording Watcher")).to_string(),
                13.0,
                Color::WHITE,
            ))
            .center_x(Length::Fill)
            .center_y(Length::Fill),
        )
        .width(Length::Fill)
        .height(Length::Fixed(42.0))
        .on_press(Message::ToggleRecording)
        .style(theme::solid_btn(
            theme::hex(0x475569),
            theme::hex(0x334155),
            theme::hex(0x64748B),
            Color::WHITE,
            8.0,
        ))
    } else {
        button(
            container(icon_label(
                IconKind::RecordDot,
                strip_leading_emoji(&app.tr("record.start_btn", "Start Recording Watcher")).to_string(),
                13.0,
                Color::WHITE,
            ))
            .center_x(Length::Fill)
            .center_y(Length::Fill),
        )
        .width(Length::Fill)
        .height(Length::Fixed(42.0))
        .on_press(Message::ToggleRecording)
        .style(theme::solid_btn(
            with_alpha(p.red, alpha),
            p.red_hover,
            with_alpha(p.red_border, alpha),
            Color::WHITE,
            8.0,
        ))
    };

    let mic_default_lbl = strip_leading_emoji(&app.tr("record.mic_default", "Default Microphone (System)")).to_string();
    let mic_options: Vec<MicOption> = app
        .mics
        .iter()
        .map(|m| MicOption {
            value: m.clone(),
            label: if m == "default" { mic_default_lbl.clone() } else { m.clone() },
        })
        .collect();
    let current_mic_val = if app.config.mic_device_name.is_empty() {
        "default".to_string()
    } else {
        app.config.mic_device_name.clone()
    };
    let mic_selected = mic_options
        .iter()
        .find(|o| o.value == current_mic_val)
        .cloned()
        .or_else(|| mic_options.first().cloned());

    let selectors_row = row![
        container(
            row![
                icon(IconKind::Target, 11.0, with_alpha(p.text_muted, alpha)),
                ui_bold(strip_leading_emoji(&app.tr("record.target_app", "Target:")).to_string())
                    .size(11)
                    .color(with_alpha(p.text_muted, alpha)),
                ui_text(strip_leading_emoji(&app.tr("record.auto_discord", "Discord Audio (Loopback)")).to_string())
                    .size(11)
                    .color(with_alpha(p.text_main, alpha)),
            ]
            .spacing(5)
            .align_y(Alignment::Center),
        )
        .padding([6, 8])
        .width(Length::FillPortion(1))
        .style(theme::selector_box_style(p)),
        container(
            row![
                icon(IconKind::Mic, 11.0, with_alpha(p.text_muted, alpha)),
                ui_bold(strip_leading_emoji(&app.tr("record.mic_input", "Mic:")).to_string())
                    .size(11)
                    .color(with_alpha(p.text_muted, alpha)),
                pick_list(mic_options, mic_selected, Message::SetMicDevice)
                    .text_size(11)
                    .font(FONT_UI)
                    .padding([3, 6])
                    .width(Length::Fill)
                    .style(theme::pick_list_style(p)),
                button(icon(IconKind::Refresh, 11.0, with_alpha(p.text_main, alpha)))
                    .padding([4, 7])
                    .on_press(Message::RefreshMics)
                    .style(theme::surface_btn(p, 5.0)),
            ]
            .spacing(5)
            .align_y(Alignment::Center),
        )
        .padding([3, 8])
        .width(Length::FillPortion(1))
        .style(theme::selector_box_style(p)),
    ]
    .spacing(8)
    .width(Length::Fill);

    let mic_muted_color = theme::hex(0xEF4444);
    let meter_box = container(
        column![
            row![
                icon(IconKind::Speaker, 11.0, with_alpha(p.text_muted, alpha)),
                ui_bold(format!("{}:", strip_leading_emoji(&app.tr("record.app_sound", "Discord"))))
                    .size(11)
                    .color(with_alpha(p.text_muted, alpha))
                    .width(Length::Fixed(72.0)),
                canvas(VuBarCanvas {
                    level: app.discord_smooth,
                    fill_color: with_alpha(p.meter_app, alpha),
                    track_color: with_alpha(p.meter_track, alpha),
                })
                .width(Length::Fill)
                .height(Length::Fixed(6.0)),
            ]
            .spacing(6)
            .align_y(Alignment::Center),
            row![
                icon(
                    if app.is_discord_muted { IconKind::MicOff } else { IconKind::Mic },
                    11.0,
                    with_alpha(
                        if app.is_discord_muted { mic_muted_color } else { p.text_muted },
                        alpha,
                    ),
                ),
                ui_bold(if app.is_discord_muted {
                    "Mic (Muted):".to_string()
                } else {
                    format!("{}:", strip_leading_emoji(&app.tr("record.microphone", "Mic")))
                })
                .size(11)
                .color(with_alpha(
                    if app.is_discord_muted { mic_muted_color } else { p.text_muted },
                    alpha,
                ))
                .width(Length::Fixed(72.0)),
                canvas(VuBarCanvas {
                    level: if app.is_discord_muted { 1.0 } else { app.mic_smooth },
                    fill_color: with_alpha(
                        if app.is_discord_muted { mic_muted_color } else { p.meter_mic },
                        alpha,
                    ),
                    track_color: if app.is_discord_muted {
                        Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.15 * alpha)
                    } else {
                        with_alpha(p.meter_track, alpha)
                    },
                })
                .width(Length::Fill)
                .height(Length::Fixed(6.0)),
            ]
            .spacing(6)
            .align_y(Alignment::Center),
        ]
        .spacing(5),
    )
    .padding([6, 10])
    .width(Length::Fill)
    .style(theme::selector_box_style(p));

    let rec_hint_left = if rec_active {
        if let Some(ref c) = clock {
            format!("Match Time: {}", c)
        } else {
            "Auto-detects match clock & pauses with sample accuracy".to_string()
        }
    } else {
        "Auto-detects match clock & pauses with sample accuracy".to_string()
    };

    let rec_hint_row = container(
        row![
            icon(IconKind::Target, 11.0, with_alpha(p.text_muted, alpha)),
            ui_text(rec_hint_left)
                .size(11)
                .color(with_alpha(p.text_muted, alpha)),
            Space::new().width(Length::Fill),
            icon(
                if app.is_discord_muted { IconKind::MicOff } else { IconKind::Mic },
                11.0,
                with_alpha(if app.is_discord_muted { mic_muted_color } else { p.green }, alpha),
            ),
            ui_bold(if app.is_discord_muted { "Muted" } else { "Live" })
                .size(11)
                .color(with_alpha(if app.is_discord_muted { mic_muted_color } else { p.green }, alpha)),
        ]
        .spacing(5)
        .align_y(Alignment::Center),
    )
    .padding([4, 8])
    .width(Length::Fill)
    .style(theme::selector_box_style(p));

    let rec_card_inner = column![
        row![
            icon(IconKind::Mic, 13.0, with_alpha(p.red_border, alpha)),
            ui_bold(strip_leading_emoji(&app.tr("record.title", "SCRIM RECORDER")).to_string())
                .size(13)
                .color(with_alpha(p.red_border, alpha)),
            Space::new().width(Length::Fill),
            container(
                row![
                    icon(rec_icon, 8.0, with_alpha(rec_pill_c, alpha)),
                    ui_bold(rec_pill_txt)
                        .size(11)
                        .color(with_alpha(rec_pill_c, alpha)),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding([3, 10])
            .style(theme::pill_style_r(rec_pill_bg, rec_pill_border, with_alpha(rec_pill_c, alpha), 6.0)),
        ]
        .spacing(6)
        .align_y(Alignment::Center),
        rec_btn,
        selectors_row,
        meter_box,
        rec_hint_row,
    ]
    .spacing(8);

    let rec_card_container = container(rec_card_inner)
        .padding([12, 14])
        .width(Length::FillPortion(1));

    let rec_card = if rec_stat.0 == "RECORDING" {
        rec_card_container.style(theme::active_card_style(p, theme::hex(0xF43F5E), pulse, alpha))
    } else if rec_stat.0 == "PAUSED" {
        rec_card_container.style(theme::active_card_style(p, theme::hex(0xFBBF24), 0.35, alpha))
    } else {
        rec_card_container.style(theme::card_style_alpha(p, alpha))
    };

    // Card 2: Replay Auto-Sync
    let (rep_icon, rep_pill_txt, rep_pill_c, rep_pill_bg, rep_pill_border) = if rep_stat.0 == "SYNCING" {
        (
            IconKind::Play,
            format!("SYNCING: {}", rep_stat.1),
            theme::hex(0x38BDF8),
            Color::from_rgba(56.0 / 255.0, 189.0 / 255.0, 248.0 / 255.0, (0.10 + 0.12 * pulse) * alpha),
            Color::from_rgba(56.0 / 255.0, 189.0 / 255.0, 248.0 / 255.0, (0.35 + 0.45 * pulse) * alpha),
        )
    } else if rep_stat.0 == "PAUSED" {
        (
            IconKind::Pause,
            format!("PAUSED: {}", rep_stat.1),
            theme::hex(0xFBBF24),
            Color::from_rgba(251.0 / 255.0, 191.0 / 255.0, 36.0 / 255.0, 0.12 * alpha),
            Color::from_rgba(251.0 / 255.0, 191.0 / 255.0, 36.0 / 255.0, 0.45 * alpha),
        )
    } else if rep_active {
        (
            IconKind::Clock,
            format!("LISTENING: {}", if rep_stat.1.is_empty() { "Waiting for replay..." } else { &rep_stat.1 }),
            p.teal,
            Color::from_rgba(10.0 / 255.0, 200.0 / 255.0, 185.0 / 255.0, 0.12 * alpha),
            Color::from_rgba(10.0 / 255.0, 200.0 / 255.0, 185.0 / 255.0, 0.45 * alpha),
        )
    } else {
        (
            IconKind::RecordDot,
            strip_leading_emoji(&app.tr("replay.status_idle", "IDLE (Click Start to detect .rofl)")).to_string(),
            p.text_muted,
            with_alpha(p.bg_card_inner, alpha),
            with_alpha(p.border_inner, alpha),
        )
    };

    let rep_btn = if rep_active {
        button(
            container(icon_label(
                IconKind::Stop,
                strip_leading_emoji(&app.tr("replay.stop_btn", "Stop Replay Auto-Sync")).to_string(),
                13.0,
                Color::WHITE,
            ))
            .center_x(Length::Fill)
            .center_y(Length::Fill),
        )
        .width(Length::Fill)
        .height(Length::Fixed(42.0))
        .on_press(Message::ToggleReplay)
        .style(theme::solid_btn(
            theme::hex(0x475569),
            theme::hex(0x334155),
            theme::hex(0x64748B),
            Color::WHITE,
            8.0,
        ))
    } else {
        button(
            container(icon_label(
                IconKind::Play,
                strip_leading_emoji(&app.tr("replay.start_btn", "Start Replay Auto-Sync")).to_string(),
                13.0,
                Color::WHITE,
            ))
            .center_x(Length::Fill)
            .center_y(Length::Fill),
        )
        .width(Length::Fill)
        .height(Length::Fixed(42.0))
        .on_press(Message::ToggleReplay)
        .style(theme::solid_btn(
            with_alpha(p.blue, alpha),
            p.blue_hover,
            with_alpha(p.blue_border, alpha),
            Color::WHITE,
            8.0,
        ))
    };

    let vol_box = container(
        row![
            icon(IconKind::Speaker, 11.0, with_alpha(p.text_muted, alpha)),
            ui_bold(format!("{}:", strip_leading_emoji(&app.tr("replay.volume", "Volume"))))
                .size(11)
                .color(with_alpha(p.text_muted, alpha)),
            slider(0..=200, app.replay_volume_pct, Message::SetReplayVolume)
                .width(Length::Fill)
                .style(theme::slider_style(p)),
            ui_bold(format!("{}%", app.replay_volume_pct))
                .size(11)
                .color(with_alpha(p.text_main, alpha))
                .width(Length::Fixed(36.0)),
            button(icon(
                if app.is_muted { IconKind::SpeakerOff } else { IconKind::Speaker },
                12.0,
                with_alpha(if app.is_muted { mic_muted_color } else { p.text_main }, alpha),
            ))
            .padding([4, 8])
            .on_press(Message::ToggleMute)
            .style(theme::surface_btn(p, 6.0)),
        ]
        .spacing(8)
        .align_y(Alignment::Center),
    )
    .padding([4, 8])
    .width(Length::Fill)
    .style(theme::selector_box_style(p));

    let api_status_el: Element<'a, Message> = if app.health.replay_api_enabled {
        row![
            icon(IconKind::Check, 11.0, with_alpha(p.green, alpha)),
            ui_bold(strip_leading_emoji(&app.tr("settings.replay_api_status_enabled", "Enabled (Port 2999)")).to_string())
                .size(11)
                .color(with_alpha(p.green, alpha)),
        ]
        .spacing(4)
        .align_y(Alignment::Center)
        .into()
    } else {
        row![
            icon(IconKind::Warning, 11.0, with_alpha(theme::hex(0xF87171), alpha)),
            ui_bold(strip_leading_emoji(&app.tr("settings.replay_api_status_disabled", "Disabled")).to_string())
                .size(11)
                .color(with_alpha(theme::hex(0xF87171), alpha)),
        ]
        .spacing(4)
        .align_y(Alignment::Center)
        .into()
    };

    let api_box = container(
        row![
            icon(IconKind::Bolt, 11.0, with_alpha(p.text_muted, alpha)),
            ui_bold(strip_leading_emoji(&app.tr("settings.replay_api_title", "Replay API:")).to_string())
                .size(11)
                .color(with_alpha(p.text_muted, alpha)),
            Space::new().width(Length::Fill),
            api_status_el,
            button(
                ui_bold(strip_leading_emoji(&app.tr("settings.enable_replay_api_btn", "Enable API")).to_string())
                    .size(11)
                    .color(Color::WHITE),
            )
            .padding([3, 10])
            .on_press(Message::EnableReplayApi)
            .style(theme::solid_btn(
                with_alpha(p.blue, alpha),
                p.blue_hover,
                with_alpha(p.blue_border, alpha),
                Color::WHITE,
                6.0,
            )),
        ]
        .spacing(8)
        .align_y(Alignment::Center),
    )
    .padding([7, 10])
    .width(Length::Fill)
    .style(theme::selector_box_style(p));

    let rep_hint_row = container(
        row![
            icon(IconKind::Lightbulb, 11.0, with_alpha(p.gold, alpha)),
            ui_text(strip_leading_emoji(&app.tr("replay.hotkey_hint", "Press M in-game to Mute/Unmute replay voice")).to_string())
                .size(11)
                .color(with_alpha(p.text_muted, alpha)),
        ]
        .spacing(6)
        .align_y(Alignment::Center),
    )
    .padding([4, 8])
    .width(Length::Fill)
    .style(theme::selector_box_style(p));

    let rep_card_inner = column![
        row![
            icon(IconKind::Film, 13.0, with_alpha(p.teal, alpha)),
            ui_bold(strip_leading_emoji(&app.tr("replay.title", "REPLAY AUTO-SYNC")).to_string())
                .size(13)
                .color(with_alpha(p.teal, alpha)),
            Space::new().width(Length::Fill),
            container(
                row![
                    icon(rep_icon, 8.0, with_alpha(rep_pill_c, alpha)),
                    ui_bold(rep_pill_txt)
                        .size(11)
                        .color(with_alpha(rep_pill_c, alpha)),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding([3, 10])
            .style(theme::pill_style_r(rep_pill_bg, rep_pill_border, with_alpha(rep_pill_c, alpha), 6.0)),
        ]
        .spacing(6)
        .align_y(Alignment::Center),
        rep_btn,
        vol_box,
        api_box,
        rep_hint_row,
    ]
    .spacing(8);

    let rep_card_container = container(rep_card_inner)
        .padding([12, 14])
        .width(Length::FillPortion(1));

    let rep_card = if rep_stat.0 == "SYNCING" {
        rep_card_container.style(theme::active_card_style(p, theme::hex(0x38BDF8), pulse, alpha))
    } else if rep_stat.0 == "PAUSED" {
        rep_card_container.style(theme::active_card_style(p, theme::hex(0xFBBF24), 0.35, alpha))
    } else {
        rep_card_container.style(theme::card_style_alpha(p, alpha))
    };

    row![rec_card, rep_card].spacing(12).width(Length::Fill).into()
}

fn view_tab_bar<'a>(app: &'a App, p: Palette, alpha: f32) -> Element<'a, Message> {
    let tab_fg = |active: bool| {
        if active {
            if p.is_dark { p.gold } else { p.teal }
        } else {
            p.text_muted
        }
    };

    container(
        row![
            button(
                container(icon_label(
                    IconKind::Clipboard,
                    strip_leading_emoji(&app.tr("tabs.log", "Activity Log")).to_string(),
                    12.0,
                    with_alpha(tab_fg(app.active_tab == Tab::Log), alpha),
                ))
                .center_x(Length::Fill),
            )
            .padding([7, 12])
            .width(Length::FillPortion(1))
            .on_press(Message::SelectTab(Tab::Log))
            .style(theme::tab_btn(p, app.active_tab == Tab::Log)),
            button(
                container(icon_label(
                    IconKind::Folder,
                    strip_leading_emoji(&app.tr("tabs.recordings", "Scrim Recordings")).to_string(),
                    12.0,
                    with_alpha(tab_fg(app.active_tab == Tab::Recordings), alpha),
                ))
                .center_x(Length::Fill),
            )
            .padding([7, 12])
            .width(Length::FillPortion(1))
            .on_press(Message::SelectTab(Tab::Recordings))
            .style(theme::tab_btn(p, app.active_tab == Tab::Recordings)),
            button(
                container(icon_label(
                    IconKind::Settings,
                    strip_leading_emoji(&app.tr("tabs.settings", "Settings")).to_string(),
                    12.0,
                    with_alpha(tab_fg(app.active_tab == Tab::Settings), alpha),
                ))
                .center_x(Length::Fill),
            )
            .padding([7, 12])
            .width(Length::FillPortion(1))
            .on_press(Message::SelectTab(Tab::Settings))
            .style(theme::tab_btn(p, app.active_tab == Tab::Settings)),
        ]
        .spacing(4)
        .width(Length::Fill),
    )
    .padding(3)
    .width(Length::Fill)
    .style(theme::tab_nav_style_alpha(p, alpha))
    .into()
}

fn debug_row<'a>(label: &'a str, val: &'a str, val_color: Color, p: Palette) -> Element<'a, Message> {
    row![
        ui_bold(label)
            .size(11)
            .color(if p.is_dark { p.text_muted } else { theme::hex(0x475569) }),
        Space::new().width(Length::Fill),
        text(val).size(11).font(FONT_MONO_BOLD).color(val_color),
    ]
    .padding([2, 0])
    .align_y(Alignment::Center)
    .into()
}

fn view_log_tab<'a>(app: &'a App, p: Palette) -> Element<'a, Message> {
    let log_lines = column(
        app.logs
            .iter()
            .rev()
            .take(500)
            .rev()
            .map(|line| {
                text(line)
                    .size(12)
                    .font(FONT_MONO)
                    .shaping(text::Shaping::Advanced)
                    .line_height(text::LineHeight::Relative(1.35))
                    .color(theme::hex(0xCBD5E1))
                    .into()
            }),
    )
    .spacing(2);

    let terminal = container(
        scrollable(log_lines)
            .id(app.log_scroll_id.clone())
            .width(Length::Fill)
            .height(Length::Fill)
            .style(theme::scrollable_style(p)),
    )
    .padding([10, 12])
    .width(Length::Fill)
    .height(Length::Fill)
    .style(theme::terminal_style(p));

    let mut split_row = row![terminal].spacing(8).width(Length::Fill).height(Length::Fill);

    if app.show_telemetry {
        let t = &app.telemetry;
        let val_default = if p.is_dark { theme::hex(0xF8FAFC) } else { theme::hex(0x0F172A) };
        let val_time = if p.is_dark { theme::hex(0x3DDCC4) } else { theme::hex(0x0891B2) };
        let val_rep = if p.is_dark { theme::hex(0xFBBF24) } else { theme::hex(0xD97706) };
        let val_ver = if p.is_dark { theme::hex(0x38BDF8) } else { theme::hex(0x0284C7) };

        let match_title_c = if p.is_dark { theme::hex(0x38BDF8) } else { theme::hex(0x0284C7) };
        let rep_title_c = if p.is_dark { theme::hex(0xFBBF24) } else { theme::hex(0xD97706) };
        let sys_title_c = if p.is_dark { theme::hex(0x34D399) } else { theme::hex(0x059669) };

        let debug_panel = container(
            scrollable(
                column![
                    icon_label(
                        IconKind::Microscope,
                        "Telemetry & Debug Panel".to_string(),
                        13.0,
                        p.teal,
                    ),
                    container(
                        column![
                            icon_label(
                                IconKind::Gamepad,
                                "Match & Recorder Engine".to_string(),
                                11.0,
                                match_title_c,
                            ),
                            debug_row("Match State:", &t.match_state, val_default, p),
                            debug_row("Game Time:", &t.game_time, val_time, p),
                            debug_row("Base Time:", &t.base_time, val_default, p),
                            debug_row("Match Game ID:", &t.game_id, val_default, p),
                            debug_row("Game Patch:", &t.game_patch, val_default, p),
                        ]
                        .spacing(3),
                    )
                    .padding([8, 10])
                    .width(Length::Fill)
                    .style(theme::debug_card_style(p)),
                    container(
                        column![
                            icon_label(
                                IconKind::Film,
                                "Replay Sync Engine".to_string(),
                                11.0,
                                rep_title_c,
                            ),
                            debug_row("Active Replay:", &t.replay_file, val_default, p),
                            debug_row("Replay Time:", &t.replay_time, val_rep, p),
                            debug_row("Player Time:", &t.player_time, val_default, p),
                            debug_row("Sync Drift:", &t.replay_drift, val_default, p),
                            debug_row("Playback Speed:", &t.replay_speed, val_default, p),
                            debug_row("Matched Scrim Audio:", &t.replay_audio, val_default, p),
                        ]
                        .spacing(3),
                    )
                    .padding([8, 10])
                    .width(Length::Fill)
                    .style(theme::debug_card_style(p)),
                    container(
                        column![
                            icon_label(
                                IconKind::Chart,
                                "System & Process Stats".to_string(),
                                11.0,
                                sys_title_c,
                            ),
                            debug_row("RAM Usage:", &t.ram_usage, val_default, p),
                            debug_row("CPU Usage:", &t.cpu_usage, val_default, p),
                            debug_row("App Version:", &t.app_version, val_ver, p),
                        ]
                        .spacing(3),
                    )
                    .padding([8, 10])
                    .width(Length::Fill)
                    .style(theme::debug_card_style(p)),
                ]
                .spacing(8),
            )
            .style(theme::scrollable_style(p)),
        )
        .padding([10, 12])
        .width(Length::Fixed(350.0))
        .height(Length::Fill)
        .style(theme::inner_card_style(p));

        split_row = split_row.push(debug_panel);
    }

    let ctrl_bar = row![
        checkbox(app.autoscroll)
            .label(app.tr("log.autoscroll", "Auto-scroll to latest"))
            .on_toggle(Message::ToggleAutoscroll)
            .text_size(11)
            .font(FONT_UI)
            .style(theme::checkbox_style(p)),
        Space::new().width(Length::Fixed(16.0)),
        checkbox(app.show_telemetry)
            .label("Show Telemetry & Debug Panel")
            .on_toggle(|_| Message::ToggleTelemetry)
            .text_size(11)
            .font(FONT_UI)
            .style(theme::checkbox_style(p)),
        Space::new().width(Length::Fill),
        button(icon_label(
            IconKind::Clipboard,
            strip_leading_emoji(&app.tr("log.copy", "Copy Log")).to_string(),
            11.0,
            p.text_main,
        ))
        .padding([4, 10])
        .on_press(Message::CopyLog)
        .style(theme::surface_btn(p, 6.0)),
        button(icon_label(
            IconKind::Trash,
            strip_leading_emoji(&app.tr("log.clear", "Clear Log")).to_string(),
            11.0,
            p.text_main,
        ))
        .padding([4, 10])
        .on_press(Message::ClearLogs)
        .style(theme::surface_btn(p, 6.0)),
    ]
    .spacing(6)
    .align_y(Alignment::Center);

    column![split_row, ctrl_bar]
        .spacing(8)
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
}

fn view_recordings_tab<'a>(app: &'a App, p: Palette) -> Element<'a, Message> {
    let local_game_ids: HashSet<String> = app
        .recordings
        .iter()
        .map(|r| {
            if r.game_id.is_empty() {
                r.filename.trim_end_matches(".mp3").trim().to_string()
            } else {
                r.game_id.trim().to_string()
            }
        })
        .collect();

    let remote_game_ids: HashSet<String> = app
        .remote_recordings
        .iter()
        .map(|it| it.game_id.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();

    let missing_remote: Vec<&CloudRecordingDto> = app
        .remote_recordings
        .iter()
        .filter(|it| {
            let gid = it.game_id.trim();
            !gid.is_empty() && !local_game_ids.contains(gid)
        })
        .collect();

    let total_bytes: u64 = app.recordings.iter().map(|r| r.size_bytes).sum();
    let total_mb = total_bytes as f64 / (1024.0 * 1024.0);
    let count_str = format!("{}", app.recordings.len());
    let size_str = format!("{:.1}", total_mb);
    let count_label = app
        .tr(
            "recordings.count_label",
            "Recordings in folder: {count} files ({size:.1} MB)",
        )
        .replace("{count}", &count_str)
        .replace("{size:.1}", &size_str)
        .replace("{size}", &size_str);

    let mut actions = row![].spacing(6).align_y(Alignment::Center);

    if !missing_remote.is_empty() {
        let dl_all_txt = strip_leading_emoji(&app.tr_fmt(
            "recordings.download_all",
            "Download All Missing ({count})",
            &[("count", &missing_remote.len().to_string())],
        ))
        .to_string();
        actions = actions.push(
            button(icon_label(IconKind::Download, dl_all_txt, 11.0, Color::WHITE))
                .padding([4, 10])
                .on_press(Message::CloudDownloadAll)
                .style(theme::solid_btn(
                    theme::hex(0x059669),
                    theme::hex(0x047857),
                    theme::hex(0x10B981),
                    Color::WHITE,
                    6.0,
                )),
        );
    }

    let sync_lbl = if app.is_syncing_remote {
        strip_leading_emoji(&app.tr("recordings.syncing", "Syncing...")).to_string()
    } else {
        strip_leading_emoji(&app.tr("recordings.sync_remote", "Sync Remote")).to_string()
    };
    let mut sync_btn = button(icon_label(IconKind::Cloud, sync_lbl, 11.0, Color::WHITE))
        .padding([4, 10])
        .style(theme::solid_btn(
            theme::hex(0x4F46E5),
            theme::hex(0x4338CA),
            theme::hex(0x6366F1),
            Color::WHITE,
            6.0,
        ));
    if !app.is_syncing_remote {
        sync_btn = sync_btn.on_press(Message::SyncRemote);
    }

    actions = actions
        .push(sync_btn)
        .push(
            button(icon_label(
                IconKind::Folder,
                strip_leading_emoji(&app.tr("recordings.open_folder", "Open Folder in Explorer")).to_string(),
                11.0,
                Color::WHITE,
            ))
            .padding([4, 10])
            .on_press(Message::OpenRecordingsFolder)
            .style(theme::solid_btn(
                p.blue,
                p.blue_hover,
                p.blue_border,
                Color::WHITE,
                6.0,
            )),
        )
        .push(
            button(icon_label(
                IconKind::Refresh,
                strip_leading_emoji(&app.tr("recordings.refresh", "Refresh")).to_string(),
                11.0,
                p.text_main,
            ))
            .padding([4, 10])
            .on_press(Message::RefreshRecordings)
            .style(theme::surface_btn(p, 6.0)),
        );

    let toolbar = row![
        ui_text(count_label).size(11).color(p.text_muted),
        Space::new().width(Length::Fill),
        actions,
    ]
    .align_y(Alignment::Center);

    let norm_current = App::normalize_patch(&app.current_client_patch);
    let can_organize = app.can_upload_to_cloud();
    let is_admin = app.is_admin_role();

    let list_col: Element<'a, Message> = if app.recordings.is_empty() && missing_remote.is_empty() {
        container(
            ui_text(app.tr_fmt(
                "recordings.empty",
                "No recordings found yet in {folder}",
                &[("folder", "recordings")],
            ))
            .size(12)
            .color(p.text_muted_dark),
        )
        .padding(40)
        .center_x(Length::Fill)
        .into()
    } else {
        let mut rows: Vec<Element<'a, Message>> = Vec::new();
        for item in missing_remote {
            rows.push(view_remote_missing_row(app, item, &norm_current, is_admin, p));
        }
        for rec in &app.recordings {
            rows.push(view_recording_row(
                app,
                rec,
                &remote_game_ids,
                &norm_current,
                can_organize,
                is_admin,
                p,
            ));
        }
        scrollable(column(rows).spacing(5))
            .width(Length::Fill)
            .height(Length::Fill)
            .style(theme::scrollable_style(p))
            .into()
    };

    column![toolbar, list_col]
        .spacing(8)
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
}

fn view_patch_badge<'a>(
    app: &'a App,
    patch: &str,
    is_outdated: bool,
    p: Palette,
) -> Element<'a, Message> {
    if is_outdated {
        let fg = if p.is_dark { theme::hex(0xFCA5A5) } else { theme::hex(0xB91C1C) };
        let bg = if p.is_dark {
            Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.20)
        } else {
            theme::hex(0xFEE2E2)
        };
        let bd = if p.is_dark { theme::hex(0xEF4444) } else { theme::hex(0xDC2626) };
        let lbl = strip_leading_emoji(&app.tr_fmt(
            "recordings.patch_outdated",
            "Patch {patch} (Outdated)",
            &[("patch", patch)],
        ))
        .to_string();
        container(
            row![
                icon(IconKind::Warning, 10.0, fg),
                ui_bold(lbl).size(11).color(fg),
            ]
            .spacing(4)
            .align_y(Alignment::Center),
        )
        .padding([1, 6])
        .style(theme::pill_style_r(bg, bd, fg, 4.0))
        .into()
    } else {
        let fg = if p.is_dark { theme::hex(0x6EE7B7) } else { theme::hex(0x047857) };
        let bg = if p.is_dark {
            Color::from_rgba(16.0 / 255.0, 185.0 / 255.0, 129.0 / 255.0, 0.12)
        } else {
            theme::hex(0xECFDF5)
        };
        let bd = if p.is_dark {
            Color::from_rgba(16.0 / 255.0, 185.0 / 255.0, 129.0 / 255.0, 0.40)
        } else {
            theme::hex(0x10B981)
        };
        let lbl = strip_leading_emoji(&app.tr_fmt(
            "recordings.patch_current",
            "Patch {patch}",
            &[("patch", patch)],
        ))
        .to_string();
        container(
            row![
                icon(IconKind::Check, 10.0, fg),
                ui_bold(lbl).size(11).color(fg),
            ]
            .spacing(4)
            .align_y(Alignment::Center),
        )
        .padding([1, 6])
        .style(theme::pill_style_r(bg, bd, fg, 4.0))
        .into()
    }
}

fn view_remote_missing_row<'a>(
    app: &'a App,
    item: &'a CloudRecordingDto,
    norm_current: &str,
    is_admin: bool,
    p: Palette,
) -> Element<'a, Message> {
    let game_id = item.game_id.trim().to_string();
    let size_mb = item.file_size.unwrap_or(0) as f64 / (1024.0 * 1024.0);
    let date_raw = item
        .created_at
        .as_deref()
        .or(item.upload_timestamp.as_deref())
        .unwrap_or("");
    let date_str = if date_raw.len() >= 10 { &date_raw[..10] } else { "Recent" };
    let remote_patch = item
        .patch
        .as_deref()
        .or(item.game_patch.as_deref())
        .unwrap_or("");
    let norm_remote = App::normalize_patch(remote_patch);
    let is_outdated = !norm_current.is_empty() && !norm_remote.is_empty() && norm_remote != norm_current;
    let date_display = if !remote_patch.is_empty() {
        format!("{}  |  Patch: {}", date_str, remote_patch)
    } else {
        date_str.to_string()
    };

    let remote_fg = if p.is_dark { theme::hex(0x38BDF8) } else { p.blue };
    let remote_bg = if p.is_dark {
        Color::from_rgba(56.0 / 255.0, 189.0 / 255.0, 248.0 / 255.0, 0.12)
    } else {
        Color::from_rgba(2.0 / 255.0, 132.0 / 255.0, 199.0 / 255.0, 0.10)
    };
    let remote_bd = Color::from_rgba(56.0 / 255.0, 189.0 / 255.0, 248.0 / 255.0, 0.30);

    let team_suffix = item
        .team_tag
        .as_deref()
        .filter(|s| !s.is_empty())
        .map(|t| format!(" • vs {}", t))
        .unwrap_or_default();
    let badge_txt = format!(
        "[{}]{}",
        app.tr("recordings.status_remote", "Remote"),
        team_suffix
    );

    let mut title_row = row![
        icon(IconKind::Cloud, 13.0, remote_fg),
        ui_bold(format!("{}.mp3", game_id)).size(13).color(remote_fg),
        container(ui_bold(badge_txt).size(11).color(remote_fg))
            .padding([1, 6])
            .style(theme::pill_style_r(remote_bg, remote_bd, remote_fg, 4.0)),
    ]
    .spacing(8)
    .align_y(Alignment::Center);

    if !remote_patch.is_empty() {
        title_row = title_row.push(view_patch_badge(app, remote_patch, is_outdated, p));
    }

    let size_fmt = format!("{:.2}", size_mb);
    let meta_txt = app
        .tr("recordings.remote_meta", "Size: {size:.2} MB  |  Uploaded: {date}")
        .replace("{size:.2}", &size_fmt)
        .replace("{size}", &size_fmt)
        .replace("{date}", &date_display);

    let is_dl = app.downloading_ids.contains(&game_id);
    let dl_lbl = if is_dl {
        strip_leading_emoji(&app.tr("recordings.downloading", "Downloading...")).to_string()
    } else {
        strip_leading_emoji(&app.tr("recordings.download", "Download")).to_string()
    };
    let mut dl_btn = button(icon_label(IconKind::Download, dl_lbl, 11.0, Color::WHITE))
        .padding([4, 10])
        .style(theme::solid_btn(
            p.blue,
            p.blue_hover,
            p.blue_border,
            Color::WHITE,
            6.0,
        ));
    if !is_dl {
        dl_btn = dl_btn.on_press(Message::CloudDownloadSingle(game_id.clone()));
    }

    let mut action_row = row![dl_btn].spacing(6).align_y(Alignment::Center);
    if is_admin {
        let is_del = app.deleting_ids.contains(&game_id);
        let del_lbl = if is_del {
            app.tr("recordings.deleting", "Deleting...")
        } else {
            strip_leading_emoji(&app.tr("recordings.delete_remote", "Delete from Server")).to_string()
        };
        let mut del_btn = button(icon_label(IconKind::Cloud, del_lbl, 11.0, Color::WHITE))
            .padding([4, 10])
            .style(theme::solid_btn(
                theme::hex(0xDC2626),
                theme::hex(0xB91C1C),
                theme::hex(0xEF4444),
                Color::WHITE,
                6.0,
            ));
        if !is_del {
            del_btn = del_btn.on_press(Message::RequestDeleteRemote(game_id));
        }
        action_row = action_row.push(del_btn);
    }

    let row_style = if is_outdated {
        container(
            row![
                column![
                    title_row,
                    ui_text(meta_txt).size(11).color(p.text_muted),
                ]
                .spacing(3)
                .width(Length::Fill),
                action_row,
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        )
        .padding([8, 12])
        .width(Length::Fill)
        .style(theme::outdated_row_style(p))
    } else {
        container(
            row![
                column![
                    title_row,
                    ui_text(meta_txt).size(11).color(p.text_muted),
                ]
                .spacing(3)
                .width(Length::Fill),
                action_row,
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        )
        .padding([8, 12])
        .width(Length::Fill)
        .style(theme::remote_missing_row_style(p))
    };

    row_style.into()
}

fn view_recording_row<'a>(
    app: &'a App,
    rec: &'a RecordingItem,
    remote_game_ids: &HashSet<String>,
    norm_current: &str,
    can_organize: bool,
    is_admin: bool,
    p: Palette,
) -> Element<'a, Message> {
    let game_id = if rec.game_id.is_empty() {
        rec.filename.trim_end_matches(".mp3").trim().to_string()
    } else {
        rec.game_id.trim().to_string()
    };
    let is_on_server = remote_game_ids.contains(&game_id);
    let norm_rec = App::normalize_patch(&rec.game_patch);
    let is_outdated = !norm_current.is_empty() && !norm_rec.is_empty() && norm_rec != norm_current;

    let is_playing = app.playing_file.as_deref() == Some(rec.filename.as_str());
    let play_btn = if is_playing {
        button(icon_label(IconKind::Stop, "Stop".to_string(), 11.0, Color::WHITE))
            .padding([4, 10])
            .on_press(Message::StopPlayback)
            .style(theme::solid_btn(
                p.red,
                p.red_hover,
                p.red_border,
                Color::WHITE,
                6.0,
            ))
    } else {
        button(icon_label(
            IconKind::Play,
            strip_leading_emoji(&app.tr("recordings.play", "Play")).to_string(),
            11.0,
            p.text_main,
        ))
        .padding([4, 10])
        .on_press(Message::PlayRecording(rec.filename.clone()))
        .style(theme::surface_btn(p, 6.0))
    };

    let local_fg = if p.is_dark { theme::hex(0x38BDF8) } else { p.blue };
    let local_bg = Color::from_rgba(56.0 / 255.0, 189.0 / 255.0, 248.0 / 255.0, 0.12);
    let local_bd = Color::from_rgba(56.0 / 255.0, 189.0 / 255.0, 248.0 / 255.0, 0.30);

    let name_el: Element<'a, Message> = if can_organize {
        button(
            row![
                icon(IconKind::Music, 13.0, p.text_main),
                ui_bold(&rec.filename).size(13).color(p.text_main),
            ]
            .spacing(6)
            .align_y(Alignment::Center),
        )
        .padding(0)
        .on_press(Message::StartEditMeta(rec.filename.clone()))
        .style(|_, _| button::Style {
            background: None,
            ..Default::default()
        })
        .into()
    } else {
        row![
            icon(IconKind::Music, 13.0, p.text_main),
            ui_bold(&rec.filename).size(13).color(p.text_main),
        ]
        .spacing(6)
        .align_y(Alignment::Center)
        .into()
    };

    let mut title_row = row![
        name_el,
        container(
            ui_bold(format!("[{}]", app.tr("recordings.status_local", "Local")))
                .size(11)
                .color(local_fg),
        )
        .padding([1, 6])
        .style(theme::pill_style_r(local_bg, local_bd, local_fg, 4.0)),
    ]
    .spacing(6)
    .align_y(Alignment::Center);

    if is_on_server {
        let sync_fg = if p.is_dark { theme::hex(0x10B981) } else { theme::hex(0x059669) };
        let sync_bg = Color::from_rgba(16.0 / 255.0, 185.0 / 255.0, 129.0 / 255.0, 0.12);
        let sync_bd = Color::from_rgba(16.0 / 255.0, 185.0 / 255.0, 129.0 / 255.0, 0.30);
        title_row = title_row.push(
            container(
                ui_bold(format!("• [{}]", app.tr("recordings.status_synced", "Synced")))
                    .size(11)
                    .color(sync_fg),
            )
            .padding([1, 6])
            .style(theme::pill_style_r(sync_bg, sync_bd, sync_fg, 4.0)),
        );
    }

    if !rec.team_tag.is_empty() {
        title_row = title_row.push(
            container(
                ui_bold(format!("• vs {}", rec.team_tag))
                    .size(11)
                    .color(local_fg),
            )
            .padding([1, 6])
            .style(theme::pill_style_r(local_bg, local_bd, local_fg, 4.0)),
        );
    }

    if !rec.game_patch.is_empty() {
        title_row = title_row.push(view_patch_badge(app, &rec.game_patch, is_outdated, p));
    }

    let recorded_date = if !rec.recorded_at.is_empty() {
        rec.recorded_at.chars().take(10).collect::<String>()
    } else {
        rec.date_str.clone()
    };
    let mut clean_notes = rec.notes.trim_start_matches('\u{FEFF}').trim().to_string();
    let lower_notes = clean_notes.to_lowercase();
    if lower_notes.contains("match length") || lower_notes.starts_with("match ended at") {
        clean_notes.clear();
    }
    let notes_preview = if clean_notes.is_empty() {
        String::new()
    } else if clean_notes.chars().count() > 35 {
        let short: String = clean_notes.chars().take(35).collect();
        format!("  |  \"{}...\"", short)
    } else {
        format!("  |  \"{}\"", clean_notes)
    };
    let patch_display = if rec.game_patch.is_empty() {
        String::new()
    } else {
        format!("  |  Patch: {}", rec.game_patch)
    };
    let meta_line = format!(
        "Size: {}  •  {}{}{}",
        rec.size_str, recorded_date, patch_display, notes_preview
    );

    let mut action_row = row![].spacing(6).align_y(Alignment::Center);

    if can_organize {
        action_row = action_row.push(
            button(icon_label(
                IconKind::Edit,
                strip_leading_emoji(&app.tr("recordings.edit_details", "Details")).to_string(),
                11.0,
                Color::WHITE,
            ))
            .padding([4, 10])
            .on_press(Message::StartEditMeta(rec.filename.clone()))
            .style(theme::solid_btn(
                theme::hex(0x2563EB),
                theme::hex(0x1D4ED8),
                theme::hex(0x3B82F6),
                Color::WHITE,
                6.0,
            )),
        );
    }

    if !is_on_server && can_organize {
        let is_up = app.uploading_ids.contains(&game_id);
        let up_lbl = if is_up {
            strip_leading_emoji(&app.tr("recordings.uploading", "Uploading...")).to_string()
        } else {
            strip_leading_emoji(&app.tr("recordings.upload", "Upload")).to_string()
        };
        let mut up_btn = button(icon_label(IconKind::Upload, up_lbl, 11.0, Color::WHITE))
            .padding([4, 10])
            .style(theme::solid_btn(
                theme::hex(0x4F46E5),
                theme::hex(0x4338CA),
                theme::hex(0x6366F1),
                Color::WHITE,
                6.0,
            ));
        if !is_up {
            up_btn = up_btn.on_press(Message::CloudUpload(game_id.clone()));
        }
        action_row = action_row.push(up_btn);
    }

    action_row = action_row.push(play_btn);

    if is_admin && is_on_server {
        let is_del = app.deleting_ids.contains(&game_id);
        let del_lbl = if is_del {
            app.tr("recordings.deleting", "Deleting...")
        } else {
            strip_leading_emoji(&app.tr("recordings.delete_remote", "Delete from Server")).to_string()
        };
        let mut del_btn = button(icon_label(IconKind::Cloud, del_lbl, 11.0, Color::WHITE))
            .padding([4, 10])
            .style(theme::solid_btn(
                theme::hex(0xDC2626),
                theme::hex(0xB91C1C),
                theme::hex(0xEF4444),
                Color::WHITE,
                6.0,
            ));
        if !is_del {
            del_btn = del_btn.on_press(Message::RequestDeleteRemote(game_id));
        }
        action_row = action_row.push(del_btn);
    }

    let del_local_fg = if p.is_dark { theme::hex(0xFCA5A5) } else { theme::hex(0xB91C1C) };
    let del_local_bg = if p.is_dark {
        Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.12)
    } else {
        theme::hex(0xFEE2E2)
    };
    let del_local_hover = if p.is_dark {
        Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.25)
    } else {
        theme::hex(0xFECACA)
    };
    let del_local_bd = if p.is_dark {
        Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.35)
    } else {
        theme::hex(0xFCA5A5)
    };

    action_row = action_row.push(
        button(icon_label(
            IconKind::Trash,
            strip_leading_emoji(&app.tr("recordings.delete_local", "Delete Local")).to_string(),
            11.0,
            del_local_fg,
        ))
        .padding([4, 10])
        .on_press(Message::RequestDeleteLocal(rec.filename.clone()))
        .style(theme::solid_btn(
            del_local_bg,
            del_local_hover,
            del_local_bd,
            del_local_fg,
            6.0,
        )),
    );

    let row_content = row![
        column![
            title_row,
            ui_text(meta_line).size(11).color(p.text_muted),
        ]
        .spacing(3)
        .width(Length::Fill),
        action_row,
    ]
    .spacing(8)
    .align_y(Alignment::Center);

    if is_outdated {
        container(row_content)
            .padding([8, 12])
            .width(Length::Fill)
            .style(theme::outdated_row_style(p))
            .into()
    } else {
        container(row_content)
            .padding([8, 12])
            .width(Length::Fill)
            .style(theme::inner_card_style(p))
            .into()
    }
}

fn view_settings_tab<'a>(app: &'a App, p: Palette) -> Element<'a, Message> {
    let cfg = &app.config;
    let folder_str = cfg.record_folder.to_string_lossy().to_string();

    let mic_default_lbl = strip_leading_emoji(&app.tr("record.mic_default", "Default Microphone (System)")).to_string();
    let mic_options: Vec<MicOption> = app
        .mics
        .iter()
        .map(|m| MicOption {
            value: m.clone(),
            label: if m == "default" { mic_default_lbl.clone() } else { m.clone() },
        })
        .collect();
    let current_mic_val = if cfg.mic_device_name.is_empty() {
        "default".to_string()
    } else {
        cfg.mic_device_name.clone()
    };
    let mic_selected = mic_options
        .iter()
        .find(|o| o.value == current_mic_val)
        .cloned()
        .or_else(|| mic_options.first().cloned());

    let lang_selected = app
        .available_languages
        .iter()
        .find(|l| l.code == cfg.language)
        .cloned()
        .or_else(|| app.available_languages.first().cloned());

    let theme_options = vec![
        ThemeOption {
            value: "dark".to_string(),
            label: strip_leading_emoji(&app.tr("settings.theme_dark", "Dark")).to_string(),
        },
        ThemeOption {
            value: "light".to_string(),
            label: strip_leading_emoji(&app.tr("settings.theme_light", "Light")).to_string(),
        },
        ThemeOption {
            value: "system".to_string(),
            label: strip_leading_emoji(&app.tr("settings.theme_system", "System Default")).to_string(),
        },
    ];
    let theme_selected = theme_options
        .iter()
        .find(|t| t.value == cfg.theme)
        .cloned()
        .or_else(|| theme_options.first().cloned());

    let opacity_pct = (cfg.hud_opacity * 100.0).round() as u32;

    // CARD 1: Paths & System Settings
    let card_paths = container(
        column![
            icon_label(
                IconKind::Folder,
                strip_leading_emoji(&app.tr("settings.paths_title", "Paths & System Settings")).to_string(),
                12.0,
                p.teal,
            ),
            row![
                ui_bold(app.tr("settings.rec_folder", "Recordings Folder:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                text_input("Recordings folder path", &folder_str)
                    .font(FONT_UI)
                    .size(11)
                    .on_input(Message::SetRecordFolder)
                    .style(theme::input_style(p))
                    .padding([5, 8])
                    .width(Length::Fill),
                button(
                    ui_bold(app.tr("settings.browse", "Browse..."))
                        .size(11)
                        .color(p.text_main),
                )
                .padding([4, 10])
                .on_press(Message::BrowseRecordFolder)
                .style(theme::surface_btn(p, 6.0)),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            row![
                ui_bold(app.tr("settings.replay_volume", "Replay Volume (%):"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                slider(0..=200, app.replay_volume_pct, Message::SetReplayVolume)
                    .width(Length::Fixed(180.0))
                    .style(theme::slider_style(p)),
                ui_bold(format!("{}%", app.replay_volume_pct))
                    .size(11)
                    .color(p.text_main),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        ]
        .spacing(8),
    )
    .padding([10, 14])
    .width(Length::Fill)
    .style(theme::settings_card_style(p));

    // CARD 2: Interface & Audio Input
    let card_interface = container(
        column![
            icon_label(
                IconKind::Globe,
                strip_leading_emoji(&app.tr("settings.interface_title", "Interface & Audio Input")).to_string(),
                12.0,
                p.teal,
            ),
            row![
                ui_bold(app.tr("settings.language", "Interface Language:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                pick_list(app.available_languages.clone(), lang_selected, Message::SetLanguage)
                    .text_size(11)
                    .font(FONT_UI)
                    .padding([6, 10])
                    .width(Length::Fixed(240.0))
                    .style(theme::pick_list_style(p)),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            row![
                ui_bold(app.tr("settings.theme", "Appearance Theme:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                pick_list(theme_options, theme_selected, Message::SetTheme)
                    .text_size(11)
                    .font(FONT_UI)
                    .padding([6, 10])
                    .width(Length::Fixed(240.0))
                    .style(theme::pick_list_style(p)),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            row![
                ui_bold(app.tr("settings.mic_device", "Microphone Input:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                pick_list(mic_options, mic_selected, Message::SetMicDevice)
                    .text_size(11)
                    .font(FONT_UI)
                    .padding([6, 10])
                    .width(Length::Fixed(420.0))
                    .style(theme::pick_list_style(p)),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        ]
        .spacing(8),
    )
    .padding([10, 14])
    .width(Length::Fill)
    .style(theme::settings_card_style(p));

    // CARD 3: Match Automation & Workflow
    let card_automation = container(
        column![
            icon_label(
                IconKind::Bolt,
                strip_leading_emoji(&app.tr("settings.automation_title", "Match Automation & Workflow")).to_string(),
                12.0,
                p.teal,
            ),
            row![
                ui_bold(app.tr("settings.auto_start_champ_select", "Auto-Start in Champ Select:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                checkbox(cfg.auto_start_champ_select)
                    .label(app.tr(
                        "settings.auto_start_champ_select_info",
                        "Automatically starts recording watcher when entering Champion Select"
                    ))
                    .on_toggle(Message::SetAutoChamp)
                    .text_size(11)
                    .font(FONT_UI)
                    .style(theme::checkbox_style(p)),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            row![
                ui_bold(app.tr("settings.auto_start_replay", "Auto-Start Replay Sync:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                checkbox(cfg.auto_start_replay)
                    .label(app.tr(
                        "settings.auto_start_replay_info",
                        "Automatically starts Replay Auto-Sync when a .rofl replay is opened in League"
                    ))
                    .on_toggle(Message::SetAutoReplay)
                    .text_size(11)
                    .font(FONT_UI)
                    .style(theme::checkbox_style(p)),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            row![
                ui_bold(app.tr("settings.minimize_to_tray", "Minimize to System Tray:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                checkbox(cfg.minimize_to_tray)
                    .label(app.tr(
                        "settings.minimize_to_tray_info",
                        "Keep running in background when minimized with titlebar button"
                    ))
                    .on_toggle(Message::SetMinimizeTray)
                    .text_size(11)
                    .font(FONT_UI)
                    .style(theme::checkbox_style(p)),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        ]
        .spacing(8),
    )
    .padding([10, 14])
    .width(Length::Fill)
    .style(theme::settings_card_style(p));

    // CARD 4: In-Game Floating HUD Overlay
    let card_hud = container(
        column![
            icon_label(
                IconKind::Gamepad,
                strip_leading_emoji(&app.tr("settings.hud_section_title", "In-Game Floating HUD Overlay")).to_string(),
                12.0,
                p.teal,
            ),
            row![
                ui_bold(app.tr("settings.hud_overlay_enabled", "Enable In-Game Overlay:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                checkbox(cfg.hud_overlay_enabled)
                    .label(app.tr(
                        "settings.hud_overlay_info",
                        "Show real-time floating audio visualizer over League of Legends"
                    ))
                    .on_toggle(Message::SetHudEnabled)
                    .text_size(11)
                    .font(FONT_UI)
                    .style(theme::checkbox_style(p)),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            row![
                ui_bold(app.tr("settings.hud_locked", "Lock Position / Click-Through:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                checkbox(cfg.hud_locked)
                    .label(app.tr(
                        "settings.hud_locked_info",
                        "Allow mouse clicks to pass through HUD directly into the game"
                    ))
                    .on_toggle(Message::SetHudLocked)
                    .text_size(11)
                    .font(FONT_UI)
                    .style(theme::checkbox_style(p)),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            row![
                ui_bold(format!("HUD Opacity ({}%):", opacity_pct))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                slider(40..=100, opacity_pct, Message::SetHudOpacity)
                    .width(Length::Fill)
                    .style(theme::slider_style(p)),
                ui_bold(format!("{}%", opacity_pct))
                    .size(11)
                    .color(p.text_main)
                    .width(Length::Fixed(35.0)),
            ]
            .spacing(10)
            .align_y(Alignment::Center),
            row![
                button(icon_label(
                    IconKind::Eye,
                    strip_leading_emoji(&app.tr("settings.hud_preview_rec_btn", "Preview Rec HUD")).to_string(),
                    11.0,
                    p.text_main,
                ))
                .padding([4, 10])
                .on_press(Message::PreviewRecHud)
                .style(theme::surface_btn(p, 6.0)),
                button(icon_label(
                    IconKind::Eye,
                    strip_leading_emoji(&app.tr("settings.hud_preview_replay_btn", "Preview Replay HUD")).to_string(),
                    11.0,
                    p.text_main,
                ))
                .padding([4, 10])
                .on_press(Message::PreviewReplayHud)
                .style(theme::surface_btn(p, 6.0)),
                button(icon_label(
                    IconKind::Close,
                    strip_leading_emoji(&app.tr("settings.hud_hide_preview_btn", "Hide Preview")).to_string(),
                    11.0,
                    p.text_main,
                ))
                .padding([4, 10])
                .on_press(Message::HideHudPreview)
                .style(theme::surface_btn(p, 6.0)),
                button(icon_label(
                    IconKind::Target,
                    strip_leading_emoji(&app.tr("settings.hud_reset_pos_btn", "Reset Position")).to_string(),
                    11.0,
                    p.text_main,
                ))
                .padding([4, 10])
                .on_press(Message::ResetHudPosition)
                .style(theme::surface_btn(p, 6.0)),
            ]
            .spacing(8),
        ]
        .spacing(8),
    )
    .padding([10, 14])
    .width(Length::Fill)
    .style(theme::settings_card_style(p));

    // CARD 5: Remote Scrim Server (Direct Port)
    let card_cloud = container(
        column![
            icon_label(
                IconKind::Cloud,
                strip_leading_emoji(&app.tr("settings.cloud_title", "Remote Scrim Server (Direct Port)")).to_string(),
                12.0,
                p.teal,
            ),
            row![
                ui_bold(app.tr("settings.cloud_url", "Server URL:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                text_input("https://your-server-ip-or-domain:8443", &cfg.remote_server_url)
                    .font(FONT_UI)
                    .size(11)
                    .on_input(Message::SetCloudUrl)
                    .style(theme::input_style(p))
                    .padding([5, 8]),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            row![
                ui_bold(app.tr("settings.cloud_api_key", "API Secret Key:"))
                    .size(11)
                    .color(p.setting_lbl)
                    .width(Length::Fixed(180.0)),
                text_input("Paste your UPLOAD_KEY or PULL_KEY", &cfg.remote_api_key)
                    .font(FONT_UI)
                    .size(11)
                    .secure(true)
                    .on_input(Message::SetCloudKey)
                    .style(theme::input_style(p))
                    .padding([5, 8]),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            row![
                button(icon_label(
                    IconKind::Bolt,
                    strip_leading_emoji(&app.tr("settings.cloud_test_btn", "Test Connection")).to_string(),
                    11.0,
                    Color::WHITE,
                ))
                .padding([5, 14])
                .on_press(Message::TestCloudConnection)
                .style(theme::solid_btn(
                    p.indigo,
                    p.indigo_hover,
                    theme::hex(0x818CF8),
                    Color::WHITE,
                    6.0,
                )),
                if !app.cloud_status_msg.is_empty() {
                    let is_err = app.cloud_status_msg.contains("⚠️") || app.cloud_status_msg.contains("✖");
                    ui_bold(strip_leading_emoji(&app.cloud_status_msg).to_string())
                        .size(11)
                        .color(if is_err { theme::hex(0xF87171) } else { p.green })
                } else {
                    ui_bold(format!("Role: {}", app.cloud_role))
                        .size(11)
                        .color(p.text_muted)
                },
            ]
            .spacing(10)
            .align_y(Alignment::Center),
        ]
        .spacing(8),
    )
    .padding([10, 14])
    .width(Length::Fill)
    .style(theme::settings_card_style(p));

    // CARD 6: Software & Updates (No manual Save Settings button!)
    let mut updates_col = column![
        icon_label(
            IconKind::Refresh,
            strip_leading_emoji(&app.tr("settings.updates_title", "Software & Updates")).to_string(),
            12.0,
            p.teal,
        ),
        row![
            ui_bold(app.tr("settings.app_version", "App Version:"))
                .size(11)
                .color(p.setting_lbl)
                .width(Length::Fixed(180.0)),
            ui_bold(format!("v{}", env!("CARGO_PKG_VERSION")))
                .size(12)
                .color(p.text_muted),
            button(icon_label(
                IconKind::Refresh,
                strip_leading_emoji(&app.tr("settings.check_updates_btn", "Check for Updates")).to_string(),
                11.0,
                p.text_main,
            ))
            .padding([4, 10])
            .on_press(Message::CheckForUpdates(false))
            .style(theme::surface_btn(p, 6.0)),
        ]
        .spacing(12)
        .align_y(Alignment::Center),
    ]
    .spacing(8);

    if !app.update_status_msg.is_empty() {
        let mut msg_row = row![
            ui_text(strip_leading_emoji(&app.update_status_msg).to_string())
                .size(11)
                .color(p.text_muted),
        ]
        .spacing(10)
        .align_y(Alignment::Center);

        if let Some(ref info) = app.update_info {
            if info.available {
                if info.download_url.is_some() {
                    let mut btn = button(icon_label(
                        IconKind::Download,
                        if app.update_downloading {
                            "Downloading...".to_string()
                        } else {
                            "Download & Install Update".to_string()
                        },
                        11.0,
                        Color::WHITE,
                    ))
                    .padding([4, 12])
                    .style(theme::solid_btn(
                        theme::hex(0x2563EB),
                        theme::hex(0x3B82F6),
                        theme::hex(0x60A5FA),
                        Color::WHITE,
                        6.0,
                    ));
                    if !app.update_downloading {
                        btn = btn.on_press(Message::ApplyUpdate);
                    }
                    msg_row = msg_row.push(btn);
                } else {
                    msg_row = msg_row.push(
                        button(
                            ui_bold("View Release")
                                .size(11)
                                .color(p.gold),
                        )
                        .padding([3, 8])
                        .on_press(Message::OpenReleasePage(
                            "https://github.com/betterix/league-voice-recorder/releases/latest"
                                .to_string(),
                        ))
                        .style(theme::surface_btn(p, 6.0)),
                    );
                }
            }
        }
        updates_col = updates_col.push(msg_row);
    }

    if app.update_downloading || app.update_progress.is_some() {
        updates_col = updates_col.push(view_update_progress_box(app, p));
    }

    let card_updates = container(updates_col)
        .padding([10, 14])
        .width(Length::Fill)
        .style(theme::settings_card_style(p));

    scrollable(
        column![
            card_paths,
            card_interface,
            card_automation,
            card_hud,
            card_cloud,
            card_updates,
        ]
        .spacing(10)
        .padding(iced::Padding::ZERO.right(4.0)),
    )
    .width(Length::Fill)
    .height(Length::Fill)
    .style(theme::scrollable_style(p))
    .into()
}

fn view_footer<'a>(app: &'a App, p: Palette, alpha: f32) -> Element<'a, Message> {
    let clock = app
        .state
        .match_time
        .lock()
        .map(|g| g.clone())
        .unwrap_or(None);
    let clock_str = match clock {
        Some(ref c) => format!("Match Time: {}", c),
        None => "Match Time: --:--".to_string(),
    };

    row![
        ui_text("League Voice Recorder • Scrimmage Voice & Replay Synchronizer")
            .size(11)
            .color(with_alpha(p.text_muted_dark, alpha)),
        Space::new().width(Length::Fill),
        ui_text(clock_str)
            .size(11)
            .color(with_alpha(p.text_muted_dark, alpha)),
    ]
    .padding([0, 4])
    .align_y(Alignment::Center)
    .width(Length::Fill)
    .into()
}
