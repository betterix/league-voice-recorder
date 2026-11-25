use iced::widget::{button, checkbox, container, pick_list, scrollable, slider, text_input};
use iced::{Background, Border, Color, Font, Shadow, Vector};

pub const FONT_UI: Font = Font::with_name("Segoe UI");
pub const FONT_UI_BOLD: Font = Font {
    weight: iced::font::Weight::Semibold,
    ..Font::with_name("Segoe UI")
};
pub const FONT_UI_HEAVY: Font = Font {
    weight: iced::font::Weight::Bold,
    ..Font::with_name("Segoe UI")
};
pub const FONT_MONO: Font = Font::with_name("Consolas");
pub const FONT_MONO_BOLD: Font = Font {
    weight: iced::font::Weight::Semibold,
    ..Font::with_name("Consolas")
};

#[inline]
pub fn with_alpha(c: Color, alpha: f32) -> Color {
    Color {
        a: (c.a * alpha).clamp(0.0, 1.0),
        ..c
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Palette {
    pub is_dark: bool,
    pub bg_dark: Color,
    pub bg_card: Color,
    pub bg_card_inner: Color,
    pub bg_terminal: Color,
    pub gold: Color,
    pub gold_light: Color,
    pub gold_dark: Color,
    pub teal: Color,
    pub teal_hover: Color,
    pub blue: Color,
    pub blue_hover: Color,
    pub blue_border: Color,
    pub red: Color,
    pub red_hover: Color,
    pub red_border: Color,
    pub green: Color,
    pub indigo: Color,
    pub indigo_hover: Color,
    pub text_main: Color,
    pub text_muted: Color,
    pub text_muted_dark: Color,
    pub border: Color,
    pub border_inner: Color,
    pub meter_mic: Color,
    pub meter_app: Color,
    pub meter_track: Color,
    pub btn_surface: Color,
    pub btn_surface_hover: Color,
    pub setting_lbl: Color,
    pub input_bg: Color,
    pub input_text: Color,
    pub scrollbar_thumb: Color,
    pub scrollbar_thumb_hover: Color,
}

pub const fn hex(rgb: u32) -> Color {
    Color::from_rgb(
        ((rgb >> 16) & 0xFF) as f32 / 255.0,
        ((rgb >> 8) & 0xFF) as f32 / 255.0,
        (rgb & 0xFF) as f32 / 255.0,
    )
}

pub const DARK: Palette = Palette {
    is_dark: true,
    bg_dark: hex(0x060B18),
    bg_card: hex(0x0B1329),
    bg_card_inner: hex(0x070D1E),
    bg_terminal: hex(0x060B18),
    gold: hex(0xC8AA6E),
    gold_light: hex(0xF0E6D2),
    gold_dark: hex(0x785A28),
    teal: hex(0x0AC8B9),
    teal_hover: hex(0x0397AB),
    blue: hex(0x0284C7),
    blue_hover: hex(0x0369A1),
    blue_border: hex(0x38BDF8),
    red: hex(0xE11D48),
    red_hover: hex(0xBE123C),
    red_border: hex(0xF43F5E),
    green: hex(0x10B981),
    indigo: hex(0x6366F1),
    indigo_hover: hex(0x4F46E5),
    text_main: hex(0xF1F5F9),
    text_muted: hex(0x94A3B8),
    text_muted_dark: hex(0x64748B),
    border: hex(0x1E2A4A),
    border_inner: hex(0x162038),
    meter_mic: hex(0x3DDCC4),
    meter_app: hex(0x8B93FF),
    meter_track: hex(0x0F172A),
    btn_surface: hex(0x0F1A36),
    btn_surface_hover: hex(0x1E2A4A),
    setting_lbl: hex(0xCBD5E1),
    input_bg: hex(0x060B18),
    input_text: hex(0xF8FAFC),
    scrollbar_thumb: hex(0x1E2A4A),
    scrollbar_thumb_hover: hex(0x2E3E66),
};

pub const LIGHT: Palette = Palette {
    is_dark: false,
    bg_dark: hex(0xF1F5F9),
    bg_card: hex(0xFFFFFF),
    bg_card_inner: hex(0xF8FAFC),
    bg_terminal: hex(0x0F172A),
    gold: hex(0x85581A),
    gold_light: hex(0x4A3310),
    gold_dark: hex(0x2F1E07),
    teal: hex(0x0D9488),
    teal_hover: hex(0x0F766E),
    blue: hex(0x0284C7),
    blue_hover: hex(0x0369A1),
    blue_border: hex(0x0284C7),
    red: hex(0xDC2626),
    red_hover: hex(0xB91C1C),
    red_border: hex(0xEF4444),
    green: hex(0x16A34A),
    indigo: hex(0x4F46E5),
    indigo_hover: hex(0x4338CA),
    text_main: hex(0x0F172A),
    text_muted: hex(0x475569),
    text_muted_dark: hex(0x64748B),
    border: hex(0xCBD5E1),
    border_inner: hex(0xE2E8F0),
    meter_mic: hex(0x0D9488),
    meter_app: hex(0x6366F1),
    meter_track: hex(0xE2E8F0),
    btn_surface: hex(0xE2E8F0),
    btn_surface_hover: hex(0xCBD5E1),
    setting_lbl: hex(0x1E293B),
    input_bg: hex(0xFFFFFF),
    input_text: hex(0x0F172A),
    scrollbar_thumb: hex(0xCBD5E1),
    scrollbar_thumb_hover: hex(0x94A3B8),
};

pub fn is_system_light_theme() -> bool {
    #[cfg(windows)]
    {
        unsafe extern "system" {
            fn RegOpenKeyExW(
                hKey: isize,
                lpSubKey: *const u16,
                ulOptions: u32,
                samDesired: u32,
                phkResult: *mut isize,
            ) -> i32;
            fn RegQueryValueExW(
                hKey: isize,
                lpValueName: *const u16,
                lpReserved: *mut u32,
                lpType: *mut u32,
                lpData: *mut u8,
                lpcbData: *mut u32,
            ) -> i32;
            fn RegCloseKey(hKey: isize) -> i32;
        }
        const HKEY_CURRENT_USER: isize = -2147483647; // 0x80000001
        const KEY_READ: u32 = 0x20019;
        let subkey: Vec<u16> = "Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize\0"
            .encode_utf16()
            .collect();
        let val_name: Vec<u16> = "AppsUseLightTheme\0".encode_utf16().collect();
        let mut hkey: isize = 0;
        if unsafe { RegOpenKeyExW(HKEY_CURRENT_USER, subkey.as_ptr(), 0, KEY_READ, &mut hkey) } == 0 {
            let mut data: u32 = 0;
            let mut data_len: u32 = 4;
            let mut val_type: u32 = 0;
            let res = unsafe {
                RegQueryValueExW(
                    hkey,
                    val_name.as_ptr(),
                    std::ptr::null_mut(),
                    &mut val_type,
                    &mut data as *mut u32 as *mut u8,
                    &mut data_len,
                )
            };
            unsafe { RegCloseKey(hkey) };
            if res == 0 {
                return data != 0;
            }
        }
    }
    false
}

pub fn is_dark_theme(theme_name: &str) -> bool {
    match theme_name {
        "light" => false,
        "system" => !is_system_light_theme(),
        _ => true,
    }
}

pub fn palette(is_dark: bool) -> Palette {
    if is_dark { DARK } else { LIGHT }
}

pub fn modal_card_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(p.bg_card)),
        border: Border {
            color: p.border,
            width: 1.0,
            radius: 12.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(0.0, 0.0, 0.0, if p.is_dark { 0.6 } else { 0.15 }),
            offset: Vector::new(0.0, 10.0),
            blur_radius: 25.0,
        },
        text_color: Some(p.text_main),
        snap: true,
    }
}

pub fn remote_missing_row_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(if p.is_dark { hex(0x0A1733) } else { hex(0xEFF6FF) })),
        border: Border {
            color: if p.is_dark { hex(0x1E3A8A) } else { hex(0x38BDF8) },
            width: 1.0,
            radius: 8.0.into(),
        },
        text_color: Some(p.text_main),
        ..Default::default()
    }
}

pub fn outdated_row_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(if p.is_dark {
            Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.10)
        } else {
            hex(0xFEE2E2)
        })),
        border: Border {
            color: hex(0xEF4444),
            width: 1.0,
            radius: 8.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(239.0 / 255.0, 68.0 / 255.0, 68.0 / 255.0, 0.22),
            offset: Vector::new(0.0, 0.0),
            blur_radius: 10.0,
        },
        text_color: Some(p.text_main),
        ..Default::default()
    }
}

pub fn app_bg_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(p.bg_dark)),
        text_color: Some(p.text_main),
        ..Default::default()
    }
}

pub fn card_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    card_style_alpha(p, 1.0)
}

pub fn card_style_alpha(p: Palette, alpha: f32) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(with_alpha(p.bg_card, alpha))),
        border: Border {
            color: with_alpha(p.border, alpha),
            width: 1.0,
            radius: 12.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(0.0, 0.0, 0.0, (if p.is_dark { 0.32 } else { 0.07 }) * alpha),
            offset: Vector::new(0.0, 3.0),
            blur_radius: 10.0,
        },
        text_color: Some(with_alpha(p.text_main, alpha)),
        snap: true,
    }
}

pub fn active_card_style(
    p: Palette,
    accent: Color,
    pulse: f32,
    alpha: f32,
) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| {
        let glow_a = (0.38 + 0.42 * pulse) * alpha;
        let shadow_a = (0.14 + 0.16 * pulse) * alpha;
        container::Style {
            background: Some(Background::Color(with_alpha(p.bg_card, alpha))),
            border: Border {
                color: Color { a: glow_a, ..accent },
                width: 1.25,
                radius: 12.0.into(),
            },
            shadow: Shadow {
                color: Color { a: shadow_a, ..accent },
                offset: Vector::new(0.0, 0.0),
                blur_radius: 16.0,
            },
            text_color: Some(with_alpha(p.text_main, alpha)),
            snap: true,
        }
    }
}

pub fn settings_card_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(p.bg_card_inner)),
        border: Border {
            color: p.border_inner,
            width: 1.0,
            radius: 10.0.into(),
        },
        text_color: Some(p.text_main),
        ..Default::default()
    }
}

pub fn inner_card_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(p.bg_card_inner)),
        border: Border {
            color: p.border_inner,
            width: 1.0,
            radius: 8.0.into(),
        },
        text_color: Some(p.text_main),
        ..Default::default()
    }
}

pub fn debug_card_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(if p.is_dark { p.bg_card } else { p.bg_card_inner })),
        border: Border {
            color: p.border_inner,
            width: 1.0,
            radius: 8.0.into(),
        },
        text_color: Some(p.text_main),
        ..Default::default()
    }
}

pub fn selector_box_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(p.bg_card_inner)),
        border: Border {
            color: p.border_inner,
            width: 1.0,
            radius: 6.0.into(),
        },
        text_color: Some(p.text_main),
        ..Default::default()
    }
}

pub fn tab_nav_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    tab_nav_style_alpha(p, 1.0)
}

pub fn tab_nav_style_alpha(p: Palette, alpha: f32) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(with_alpha(p.bg_card, alpha))),
        border: Border {
            color: with_alpha(p.border, alpha),
            width: 1.0,
            radius: 10.0.into(),
        },
        text_color: Some(with_alpha(p.text_main, alpha)),
        snap: true,
        ..Default::default()
    }
}

pub fn terminal_style(p: Palette) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(p.bg_terminal)),
        border: Border {
            color: p.border_inner,
            width: 1.0,
            radius: 8.0.into(),
        },
        text_color: Some(hex(0xCBD5E1)),
        ..Default::default()
    }
}

pub fn pill_style_r(
    bg: Color,
    border_c: Color,
    text_c: Color,
    radius: f32,
) -> impl Fn(&iced::Theme) -> container::Style {
    move |_| container::Style {
        background: Some(Background::Color(bg)),
        border: Border {
            color: border_c,
            width: 1.0,
            radius: radius.into(),
        },
        text_color: Some(text_c),
        ..Default::default()
    }
}

pub fn pill_style(
    bg: Color,
    border_c: Color,
    text_c: Color,
) -> impl Fn(&iced::Theme) -> container::Style {
    pill_style_r(bg, border_c, text_c, 12.0)
}

pub fn solid_btn(
    bg: Color,
    hover_bg: Color,
    border_c: Color,
    text_c: Color,
    radius: f32,
) -> impl Fn(&iced::Theme, button::Status) -> button::Style {
    move |_, status| {
        let active_bg = match status {
            button::Status::Hovered | button::Status::Pressed => hover_bg,
            button::Status::Disabled => Color { a: 0.55, ..bg },
            _ => bg,
        };
        button::Style {
            background: Some(Background::Color(active_bg)),
            text_color: text_c,
            border: Border {
                color: border_c,
                width: 1.0,
                radius: radius.into(),
            },
            shadow: Shadow {
                color: Color::from_rgba(0.0, 0.0, 0.0, 0.25),
                offset: Vector::new(0.0, 2.0),
                blur_radius: 6.0,
            },
            snap: true,
        }
    }
}

pub fn surface_btn(
    p: Palette,
    radius: f32,
) -> impl Fn(&iced::Theme, button::Status) -> button::Style {
    move |_, status| {
        let active_bg = match status {
            button::Status::Hovered | button::Status::Pressed => p.btn_surface_hover,
            button::Status::Disabled => Color { a: 0.55, ..p.btn_surface },
            _ => p.btn_surface,
        };
        button::Style {
            background: Some(Background::Color(active_bg)),
            text_color: p.text_main,
            border: Border {
                color: p.border,
                width: 1.0,
                radius: radius.into(),
            },
            ..Default::default()
        }
    }
}

pub fn tab_btn(
    p: Palette,
    active: bool,
) -> impl Fn(&iced::Theme, button::Status) -> button::Style {
    move |_, status| {
        let bg = if active {
            if p.is_dark { p.bg_card_inner } else { hex(0xFFFFFF) }
        } else if matches!(status, button::Status::Hovered) {
            if p.is_dark {
                Color::from_rgba(1.0, 1.0, 1.0, 0.03)
            } else {
                Color::from_rgba(0.0, 0.0, 0.0, 0.04)
            }
        } else {
            Color::TRANSPARENT
        };
        let text_color = if active {
            if p.is_dark { p.gold } else { p.teal }
        } else if matches!(status, button::Status::Hovered) {
            p.text_main
        } else {
            p.text_muted
        };
        let border_color = if active {
            if p.is_dark {
                Color { a: 0.45, ..p.gold }
            } else {
                Color { a: 0.45, ..p.teal }
            }
        } else {
            Color::TRANSPARENT
        };
        button::Style {
            background: Some(Background::Color(bg)),
            text_color,
            border: Border {
                color: border_color,
                width: if active { 1.0 } else { 0.0 },
                radius: 7.0.into(),
            },
            shadow: if active {
                Shadow {
                    color: Color::from_rgba(0.0, 0.0, 0.0, 0.18),
                    offset: Vector::new(0.0, 1.0),
                    blur_radius: 4.0,
                }
            } else {
                Shadow::default()
            },
            snap: true,
        }
    }
}

pub fn input_style(p: Palette) -> impl Fn(&iced::Theme, text_input::Status) -> text_input::Style {
    move |_, status| text_input::Style {
        background: Background::Color(p.input_bg),
        border: Border {
            color: match status {
                text_input::Status::Focused { .. } => p.teal,
                _ => p.border,
            },
            width: 1.0,
            radius: 6.0.into(),
        },
        icon: p.text_muted,
        placeholder: p.text_muted_dark,
        value: p.input_text,
        selection: Color { a: 0.35, ..p.blue },
    }
}

pub fn pick_list_style(p: Palette) -> impl Fn(&iced::Theme, pick_list::Status) -> pick_list::Style {
    move |_, status| pick_list::Style {
        text_color: p.input_text,
        placeholder_color: p.text_muted,
        handle_color: p.text_muted,
        background: Background::Color(p.input_bg),
        border: Border {
            color: match status {
                pick_list::Status::Opened { .. } => p.teal,
                pick_list::Status::Hovered => p.teal,
                _ => p.border,
            },
            width: 1.0,
            radius: 6.0.into(),
        },
    }
}

pub fn checkbox_style(p: Palette) -> impl Fn(&iced::Theme, checkbox::Status) -> checkbox::Style {
    move |_, status| {
        let is_checked = matches!(
            status,
            checkbox::Status::Active { is_checked: true }
                | checkbox::Status::Hovered { is_checked: true }
        );
        checkbox::Style {
            background: Background::Color(if is_checked {
                p.teal
            } else {
                p.border
            }),
            icon_color: Color::WHITE,
            border: Border {
                color: if is_checked { p.teal } else { p.border },
                width: 1.0,
                radius: 4.0.into(),
            },
            text_color: Some(p.text_muted),
        }
    }
}

pub fn slider_style(p: Palette) -> impl Fn(&iced::Theme, slider::Status) -> slider::Style {
    move |_, status| {
        let handle_color = match status {
            slider::Status::Hovered | slider::Status::Dragged => p.teal_hover,
            _ => p.teal,
        };
        slider::Style {
            rail: slider::Rail {
                backgrounds: (
                    Background::Color(p.teal),
                    Background::Color(p.meter_track),
                ),
                width: 6.0,
                border: Border {
                    radius: 3.0.into(),
                    width: 0.0,
                    color: Color::TRANSPARENT,
                },
            },
            handle: slider::Handle {
                shape: slider::HandleShape::Circle { radius: 7.0 },
                background: Background::Color(handle_color),
                border_width: 1.5,
                border_color: Color::WHITE,
            },
        }
    }
}

pub fn scrollable_style(p: Palette) -> impl Fn(&iced::Theme, scrollable::Status) -> scrollable::Style {
    move |_, status| {
        let thumb_color = match status {
            scrollable::Status::Hovered { .. } | scrollable::Status::Dragged { .. } => {
                p.scrollbar_thumb_hover
            }
            _ => p.scrollbar_thumb,
        };
        let rail = scrollable::Rail {
            background: Some(Background::Color(Color { a: 0.4, ..p.bg_dark })),
            border: Border {
                radius: 4.0.into(),
                width: 0.0,
                color: Color::TRANSPARENT,
            },
            scroller: scrollable::Scroller {
                background: Background::Color(thumb_color),
                border: Border {
                    radius: 4.0.into(),
                    width: 1.0,
                    color: Color::from_rgba(1.0, 1.0, 1.0, 0.05),
                },
            },
        };
        scrollable::Style {
            container: container::Style::default(),
            vertical_rail: rail,
            horizontal_rail: rail,
            gap: None,
            auto_scroll: scrollable::AutoScroll {
                background: Background::Color(p.bg_card),
                border: Border::default(),
                shadow: Shadow::default(),
                icon: p.text_muted,
            },
        }
    }
}
