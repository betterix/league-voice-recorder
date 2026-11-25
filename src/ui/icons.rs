use iced::widget::canvas;
use iced::{mouse, Color, Element, Length, Point, Rectangle, Renderer, Size, Theme};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IconKind {
    Mic,
    MicOff,
    Speaker,
    SpeakerOff,
    RecordDot,
    Stop,
    Play,
    Pause,
    Refresh,
    Moon,
    Sun,
    Monitor,
    Rocket,
    Bolt,
    Lightbulb,
    Target,
    Clipboard,
    Folder,
    Settings,
    Trash,
    Cloud,
    Download,
    Upload,
    Edit,
    Lock,
    Unlock,
    Eye,
    Close,
    Check,
    Warning,
    Globe,
    Gamepad,
    Film,
    Microscope,
    Chart,
    Clock,
    Music,
}

struct VectorIcon {
    kind: IconKind,
    color: Color,
}

impl<M> canvas::Program<M> for VectorIcon {
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
        let s = bounds.width.min(bounds.height);
        let c = self.color;
        let stroke = |w: f32| canvas::Stroke {
            style: canvas::Style::Solid(c),
            width: (w * s / 16.0).max(1.1),
            line_cap: canvas::LineCap::Round,
            line_join: canvas::LineJoin::Round,
            ..Default::default()
        };
        let p = |x: f32, y: f32| Point::new(x * s / 16.0, y * s / 16.0);
        let r = |v: f32| v * s / 16.0;

        match self.kind {
            IconKind::RecordDot => {
                frame.fill(&canvas::Path::circle(p(8.0, 8.0), r(5.0)), c);
            }
            IconKind::Stop => {
                frame.fill_rectangle(p(3.5, 3.5), Size::new(r(9.0), r(9.0)), c);
            }
            IconKind::Play => {
                let path = canvas::Path::new(|b| {
                    b.move_to(p(4.0, 2.8));
                    b.line_to(p(13.2, 8.0));
                    b.line_to(p(4.0, 13.2));
                    b.close();
                });
                frame.fill(&path, c);
            }
            IconKind::Pause => {
                frame.fill_rectangle(p(3.8, 3.0), Size::new(r(3.0), r(10.0)), c);
                frame.fill_rectangle(p(9.2, 3.0), Size::new(r(3.0), r(10.0)), c);
            }
            IconKind::Close => {
                let path = canvas::Path::new(|b| {
                    b.move_to(p(3.5, 3.5));
                    b.line_to(p(12.5, 12.5));
                    b.move_to(p(12.5, 3.5));
                    b.line_to(p(3.5, 12.5));
                });
                frame.stroke(&path, stroke(1.8));
            }
            IconKind::Check => {
                let path = canvas::Path::new(|b| {
                    b.move_to(p(2.8, 8.5));
                    b.line_to(p(6.4, 12.0));
                    b.line_to(p(13.2, 4.2));
                });
                frame.stroke(&path, stroke(1.8));
            }
            IconKind::Warning => {
                let tri = canvas::Path::new(|b| {
                    b.move_to(p(8.0, 2.2));
                    b.line_to(p(14.2, 13.5));
                    b.line_to(p(1.8, 13.5));
                    b.close();
                });
                frame.stroke(&tri, stroke(1.5));
                let ex = canvas::Path::new(|b| {
                    b.move_to(p(8.0, 6.2));
                    b.line_to(p(8.0, 9.5));
                });
                frame.stroke(&ex, stroke(1.6));
                frame.fill(&canvas::Path::circle(p(8.0, 11.6), r(0.9)), c);
            }
            IconKind::Mic => {
                let capsule = canvas::Path::rounded_rectangle(p(5.8, 2.0), Size::new(r(4.4), r(7.2)), r(2.2).into());
                frame.stroke(&capsule, stroke(1.5));
                let stand = canvas::Path::new(|b| {
                    b.arc(canvas::path::Arc {
                        center: p(8.0, 7.6),
                        radius: r(4.2),
                        start_angle: iced::Radians(0.15),
                        end_angle: iced::Radians(std::f32::consts::PI - 0.15),
                    });
                    b.move_to(p(8.0, 11.8));
                    b.line_to(p(8.0, 14.2));
                    b.move_to(p(5.2, 14.2));
                    b.line_to(p(10.8, 14.2));
                });
                frame.stroke(&stand, stroke(1.4));
            }
            IconKind::MicOff => {
                let capsule = canvas::Path::rounded_rectangle(p(5.8, 2.0), Size::new(r(4.4), r(7.2)), r(2.2).into());
                frame.stroke(&capsule, stroke(1.4));
                let stand = canvas::Path::new(|b| {
                    b.move_to(p(8.0, 11.8));
                    b.line_to(p(8.0, 14.2));
                    b.move_to(p(5.2, 14.2));
                    b.line_to(p(10.8, 14.2));
                    b.move_to(p(2.5, 2.5));
                    b.line_to(p(13.5, 13.5));
                });
                frame.stroke(&stand, stroke(1.6));
            }
            IconKind::Speaker => {
                let cone = canvas::Path::new(|b| {
                    b.move_to(p(2.5, 6.0));
                    b.line_to(p(5.2, 6.0));
                    b.line_to(p(8.8, 3.0));
                    b.line_to(p(8.8, 13.0));
                    b.line_to(p(5.2, 10.0));
                    b.line_to(p(2.5, 10.0));
                    b.close();
                });
                frame.fill(&cone, c);
                let waves = canvas::Path::new(|b| {
                    b.arc(canvas::path::Arc {
                        center: p(8.8, 8.0),
                        radius: r(2.8),
                        start_angle: iced::Radians(-0.8),
                        end_angle: iced::Radians(0.8),
                    });
                    b.move_to(p(11.6, 4.2));
                    b.arc(canvas::path::Arc {
                        center: p(8.8, 8.0),
                        radius: r(4.8),
                        start_angle: iced::Radians(-0.85),
                        end_angle: iced::Radians(0.85),
                    });
                });
                frame.stroke(&waves, stroke(1.4));
            }
            IconKind::SpeakerOff => {
                let cone = canvas::Path::new(|b| {
                    b.move_to(p(2.5, 6.0));
                    b.line_to(p(5.2, 6.0));
                    b.line_to(p(8.8, 3.0));
                    b.line_to(p(8.8, 13.0));
                    b.line_to(p(5.2, 10.0));
                    b.line_to(p(2.5, 10.0));
                    b.close();
                });
                frame.fill(&cone, c);
                let cross = canvas::Path::new(|b| {
                    b.move_to(p(10.8, 5.5));
                    b.line_to(p(14.2, 10.5));
                    b.move_to(p(14.2, 5.5));
                    b.line_to(p(10.8, 10.5));
                });
                frame.stroke(&cross, stroke(1.6));
            }
            IconKind::Refresh => {
                let arc = canvas::Path::new(|b| {
                    b.arc(canvas::path::Arc {
                        center: p(8.0, 8.0),
                        radius: r(5.0),
                        start_angle: iced::Radians(-0.4),
                        end_angle: iced::Radians(std::f32::consts::PI * 1.45),
                    });
                });
                frame.stroke(&arc, stroke(1.6));
                let arrow = canvas::Path::new(|b| {
                    b.move_to(p(12.2, 3.0));
                    b.line_to(p(13.0, 6.2));
                    b.line_to(p(9.8, 5.8));
                });
                frame.stroke(&arrow, stroke(1.5));
            }
            IconKind::Moon => {
                let moon = canvas::Path::new(|b| {
                    b.arc(canvas::path::Arc {
                        center: p(8.5, 8.0),
                        radius: r(5.2),
                        start_angle: iced::Radians(0.75),
                        end_angle: iced::Radians(std::f32::consts::PI * 1.75),
                    });
                });
                frame.stroke(&moon, stroke(1.8));
            }
            IconKind::Sun => {
                frame.fill(&canvas::Path::circle(p(8.0, 8.0), r(3.0)), c);
                let rays = canvas::Path::new(|b| {
                    for i in 0..8 {
                        let a = (i as f32) * std::f32::consts::FRAC_PI_4;
                        let (sa, ca) = a.sin_cos();
                        b.move_to(p(8.0 + ca * 4.5, 8.0 + sa * 4.5));
                        b.line_to(p(8.0 + ca * 6.4, 8.0 + sa * 6.4));
                    }
                });
                frame.stroke(&rays, stroke(1.4));
            }
            IconKind::Monitor => {
                let screen = canvas::Path::rounded_rectangle(p(2.0, 2.8), Size::new(r(12.0), r(8.2)), r(1.5).into());
                frame.stroke(&screen, stroke(1.4));
                let base = canvas::Path::new(|b| {
                    b.move_to(p(8.0, 11.0));
                    b.line_to(p(8.0, 13.6));
                    b.move_to(p(5.0, 13.6));
                    b.line_to(p(11.0, 13.6));
                });
                frame.stroke(&base, stroke(1.5));
            }
            IconKind::Rocket => {
                let body = canvas::Path::new(|b| {
                    b.move_to(p(8.0, 2.0));
                    b.line_to(p(11.2, 6.2));
                    b.line_to(p(10.5, 11.2));
                    b.line_to(p(5.5, 11.2));
                    b.line_to(p(4.8, 6.2));
                    b.close();
                    b.move_to(p(6.8, 11.2));
                    b.line_to(p(8.0, 14.2));
                    b.line_to(p(9.2, 11.2));
                });
                frame.stroke(&body, stroke(1.4));
                frame.fill(&canvas::Path::circle(p(8.0, 6.5), r(1.1)), c);
            }
            IconKind::Bolt => {
                let bolt = canvas::Path::new(|b| {
                    b.move_to(p(9.2, 1.8));
                    b.line_to(p(4.0, 8.8));
                    b.line_to(p(8.0, 8.8));
                    b.line_to(p(6.8, 14.2));
                    b.line_to(p(12.0, 7.2));
                    b.line_to(p(8.0, 7.2));
                    b.close();
                });
                frame.fill(&bolt, c);
            }
            IconKind::Lightbulb => {
                let bulb = canvas::Path::circle(p(8.0, 6.2), r(3.8));
                frame.stroke(&bulb, stroke(1.4));
                let base = canvas::Path::new(|b| {
                    b.move_to(p(6.2, 10.5));
                    b.line_to(p(9.8, 10.5));
                    b.move_to(p(6.6, 12.6));
                    b.line_to(p(9.4, 12.6));
                });
                frame.stroke(&base, stroke(1.5));
            }
            IconKind::Target => {
                frame.stroke(&canvas::Path::circle(p(8.0, 8.0), r(5.6)), stroke(1.4));
                frame.stroke(&canvas::Path::circle(p(8.0, 8.0), r(2.8)), stroke(1.3));
                frame.fill(&canvas::Path::circle(p(8.0, 8.0), r(1.1)), c);
            }
            IconKind::Clipboard => {
                let board = canvas::Path::rounded_rectangle(p(3.2, 3.2), Size::new(r(9.6), r(11.0)), r(1.4).into());
                frame.stroke(&board, stroke(1.4));
                let clip = canvas::Path::rounded_rectangle(p(5.8, 1.8), Size::new(r(4.4), r(2.4)), r(0.8).into());
                frame.fill(&clip, c);
                let lines = canvas::Path::new(|b| {
                    b.move_to(p(5.5, 7.2));
                    b.line_to(p(10.5, 7.2));
                    b.move_to(p(5.5, 10.2));
                    b.line_to(p(9.5, 10.2));
                });
                frame.stroke(&lines, stroke(1.3));
            }
            IconKind::Folder => {
                let folder = canvas::Path::new(|b| {
                    b.move_to(p(2.0, 3.5));
                    b.line_to(p(6.2, 3.5));
                    b.line_to(p(7.6, 5.0));
                    b.line_to(p(14.0, 5.0));
                    b.line_to(p(14.0, 12.8));
                    b.line_to(p(2.0, 12.8));
                    b.close();
                });
                frame.stroke(&folder, stroke(1.4));
            }
            IconKind::Settings => {
                frame.stroke(&canvas::Path::circle(p(8.0, 8.0), r(2.4)), stroke(1.5));
                frame.stroke(&canvas::Path::circle(p(8.0, 8.0), r(4.8)), stroke(1.4));
                let teeth = canvas::Path::new(|b| {
                    for i in 0..8 {
                        let a = (i as f32) * std::f32::consts::FRAC_PI_4;
                        let (sa, ca) = a.sin_cos();
                        b.move_to(p(8.0 + ca * 4.8, 8.0 + sa * 4.8));
                        b.line_to(p(8.0 + ca * 6.5, 8.0 + sa * 6.5));
                    }
                });
                frame.stroke(&teeth, stroke(1.5));
            }
            IconKind::Trash => {
                let path = canvas::Path::new(|b| {
                    b.move_to(p(3.0, 4.2));
                    b.line_to(p(13.0, 4.2));
                    b.move_to(p(6.0, 4.2));
                    b.line_to(p(6.5, 2.4));
                    b.line_to(p(9.5, 2.4));
                    b.line_to(p(10.0, 4.2));
                    b.move_to(p(4.4, 4.2));
                    b.line_to(p(5.0, 13.5));
                    b.line_to(p(11.0, 13.5));
                    b.line_to(p(11.6, 4.2));
                });
                frame.stroke(&path, stroke(1.4));
            }
            IconKind::Cloud => {
                let cloud = canvas::Path::new(|b| {
                    b.move_to(p(4.2, 12.2));
                    b.line_to(p(11.8, 12.2));
                    b.arc(canvas::path::Arc {
                        center: p(11.5, 9.6),
                        radius: r(2.6),
                        start_angle: iced::Radians(std::f32::consts::FRAC_PI_2),
                        end_angle: iced::Radians(-std::f32::consts::FRAC_PI_2),
                    });
                    b.arc(canvas::path::Arc {
                        center: p(8.0, 7.2),
                        radius: r(3.4),
                        start_angle: iced::Radians(0.0),
                        end_angle: iced::Radians(std::f32::consts::PI),
                    });
                    b.arc(canvas::path::Arc {
                        center: p(4.5, 9.8),
                        radius: r(2.4),
                        start_angle: iced::Radians(-std::f32::consts::FRAC_PI_2),
                        end_angle: iced::Radians(std::f32::consts::FRAC_PI_2),
                    });
                });
                frame.stroke(&cloud, stroke(1.4));
            }
            IconKind::Download => {
                let arrow = canvas::Path::new(|b| {
                    b.move_to(p(8.0, 2.5));
                    b.line_to(p(8.0, 10.5));
                    b.move_to(p(4.8, 7.5));
                    b.line_to(p(8.0, 10.8));
                    b.line_to(p(11.2, 7.5));
                    b.move_to(p(3.0, 13.2));
                    b.line_to(p(13.0, 13.2));
                });
                frame.stroke(&arrow, stroke(1.6));
            }
            IconKind::Upload => {
                let arrow = canvas::Path::new(|b| {
                    b.move_to(p(8.0, 10.8));
                    b.line_to(p(8.0, 2.8));
                    b.move_to(p(4.8, 5.8));
                    b.line_to(p(8.0, 2.5));
                    b.line_to(p(11.2, 5.8));
                    b.move_to(p(3.0, 13.2));
                    b.line_to(p(13.0, 13.2));
                });
                frame.stroke(&arrow, stroke(1.6));
            }
            IconKind::Edit => {
                let pen = canvas::Path::new(|b| {
                    b.move_to(p(10.8, 2.6));
                    b.line_to(p(13.4, 5.2));
                    b.line_to(p(5.6, 13.0));
                    b.line_to(p(2.6, 13.4));
                    b.line_to(p(3.0, 10.4));
                    b.close();
                });
                frame.stroke(&pen, stroke(1.4));
            }
            IconKind::Lock => {
                let body = canvas::Path::rounded_rectangle(p(3.5, 7.0), Size::new(r(9.0), r(6.8)), r(1.5).into());
                frame.stroke(&body, stroke(1.4));
                let shackle = canvas::Path::new(|b| {
                    b.arc(canvas::path::Arc {
                        center: p(8.0, 5.8),
                        radius: r(2.6),
                        start_angle: iced::Radians(std::f32::consts::PI),
                        end_angle: iced::Radians(0.0),
                    });
                });
                frame.stroke(&shackle, stroke(1.4));
            }
            IconKind::Unlock => {
                let body = canvas::Path::rounded_rectangle(p(3.5, 7.0), Size::new(r(9.0), r(6.8)), r(1.5).into());
                frame.stroke(&body, stroke(1.4));
                let shackle = canvas::Path::new(|b| {
                    b.arc(canvas::path::Arc {
                        center: p(9.6, 5.2),
                        radius: r(2.6),
                        start_angle: iced::Radians(std::f32::consts::PI),
                        end_angle: iced::Radians(0.0),
                    });
                });
                frame.stroke(&shackle, stroke(1.4));
            }
            IconKind::Eye => {
                let eye = canvas::Path::new(|b| {
                    b.move_to(p(1.8, 8.0));
                    b.quadratic_curve_to(p(8.0, 2.5), p(14.2, 8.0));
                    b.quadratic_curve_to(p(8.0, 13.5), p(1.8, 8.0));
                });
                frame.stroke(&eye, stroke(1.4));
                frame.fill(&canvas::Path::circle(p(8.0, 8.0), r(1.8)), c);
            }
            IconKind::Globe => {
                frame.stroke(&canvas::Path::circle(p(8.0, 8.0), r(5.6)), stroke(1.4));
                let lines = canvas::Path::new(|b| {
                    b.move_to(p(2.4, 8.0));
                    b.line_to(p(13.6, 8.0));
                    b.move_to(p(8.0, 2.4));
                    b.line_to(p(8.0, 13.6));
                });
                frame.stroke(&lines, stroke(1.2));
            }
            IconKind::Gamepad => {
                let pad = canvas::Path::rounded_rectangle(p(2.0, 4.5), Size::new(r(12.0), r(7.2)), r(3.0).into());
                frame.stroke(&pad, stroke(1.4));
                let dpad = canvas::Path::new(|b| {
                    b.move_to(p(4.4, 8.1));
                    b.line_to(p(6.8, 8.1));
                    b.move_to(p(5.6, 6.9));
                    b.line_to(p(5.6, 9.3));
                });
                frame.stroke(&dpad, stroke(1.3));
                frame.fill(&canvas::Path::circle(p(10.8, 7.3), r(0.9)), c);
                frame.fill(&canvas::Path::circle(p(9.6, 9.0), r(0.9)), c);
            }
            IconKind::Film => {
                let box_p = canvas::Path::rounded_rectangle(p(2.4, 3.0), Size::new(r(11.2), r(10.0)), r(1.4).into());
                frame.stroke(&box_p, stroke(1.4));
                let lines = canvas::Path::new(|b| {
                    b.move_to(p(5.0, 3.0));
                    b.line_to(p(5.0, 13.0));
                    b.move_to(p(11.0, 3.0));
                    b.line_to(p(11.0, 13.0));
                });
                frame.stroke(&lines, stroke(1.2));
            }
            IconKind::Microscope => {
                let path = canvas::Path::new(|b| {
                    b.move_to(p(9.5, 2.4));
                    b.line_to(p(11.5, 4.4));
                    b.line_to(p(7.5, 8.4));
                    b.line_to(p(5.5, 6.4));
                    b.close();
                    b.move_to(p(4.0, 13.6));
                    b.line_to(p(12.0, 13.6));
                    b.move_to(p(9.0, 6.8));
                    b.line_to(p(10.5, 13.6));
                });
                frame.stroke(&path, stroke(1.4));
            }
            IconKind::Chart => {
                frame.fill_rectangle(p(3.0, 8.5), Size::new(r(2.4), r(5.0)), c);
                frame.fill_rectangle(p(6.8, 4.5), Size::new(r(2.4), r(9.0)), c);
                frame.fill_rectangle(p(10.6, 6.5), Size::new(r(2.4), r(7.0)), c);
            }
            IconKind::Clock => {
                frame.stroke(&canvas::Path::circle(p(8.0, 8.0), r(5.5)), stroke(1.4));
                let hands = canvas::Path::new(|b| {
                    b.move_to(p(8.0, 4.8));
                    b.line_to(p(8.0, 8.2));
                    b.line_to(p(10.4, 9.6));
                });
                frame.stroke(&hands, stroke(1.4));
            }
            IconKind::Music => {
                let note = canvas::Path::new(|b| {
                    b.move_to(p(6.2, 11.4));
                    b.line_to(p(6.2, 3.6));
                    b.line_to(p(12.4, 2.6));
                    b.line_to(p(12.4, 10.4));
                });
                frame.stroke(&note, stroke(1.4));
                frame.fill(&canvas::Path::circle(p(4.8, 11.8), r(1.8)), c);
                frame.fill(&canvas::Path::circle(p(11.0, 10.8), r(1.8)), c);
            }
        }

        vec![frame.into_geometry()]
    }
}

pub fn icon<'a, M: 'a>(kind: IconKind, size: f32, color: Color) -> Element<'a, M> {
    canvas(VectorIcon { kind, color })
        .width(Length::Fixed(size))
        .height(Length::Fixed(size))
        .into()
}

fn is_emoji_or_symbol(ch: char) -> bool {
    let cp = ch as u32;
    matches!(
        cp,
        0x200D
            | 0x20E3
            | 0x2190..=0x2BFF
            | 0xFE00..=0xFE0F
            | 0x1F000..=0x1FAFF
    )
}

/// Strip leading emoji/symbol prefixes from localized strings so crisp vector icons
/// can be rendered beside them without duplicate or broken emoji glyphs.
pub fn strip_leading_emoji(s: &str) -> &str {
    let mut idx = 0;
    for (i, ch) in s.char_indices() {
        if is_emoji_or_symbol(ch) || ch == ' ' {
            idx = i + ch.len_utf8();
        } else {
            break;
        }
    }
    s[idx..].trim_start()
}
