# README banners & badge references

This folder centralizes the dynamic image URLs used in the top-level `README.md`. Storing them here keeps the README clean and makes it trivial to swap colors, copy or themes across the repo.

All banners are rendered on demand by [kyechan99/capsule-render](https://github.com/kyechan99/capsule-render). All static badges are rendered by [shields.io](https://shields.io).

## Header banner (top of README)

```
https://capsule-render.vercel.app/api?type=waving&color=0:326CE5,50:1A73E8,100:0A7EA4&height=220&section=header&text=ShiftFestival%20Infra&fontSize=58&fontAlignY=38&fontColor=ffffff&desc=Kubernetes%20%E2%80%A2%20Kustomize%20%E2%80%A2%20GitOps&descAlignY=62&descSize=18&animation=fadeIn
```

Parameters worth knowing:

| Param            | Effect                                                  |
|------------------|---------------------------------------------------------|
| `type`           | `waving`, `rect`, `slice`, `cylinder`, `soft`, `egg`    |
| `color`          | Single hex (`326CE5`) or gradient (`0:AAA,100:BBB`)     |
| `height`         | Banner height in px                                     |
| `text`           | URL-encoded title                                       |
| `desc`           | URL-encoded subtitle                                    |
| `animation`      | `fadeIn`, `twinkling`, `blinking`, `scaleIn`            |

## Footer banner

```
https://capsule-render.vercel.app/api?type=waving&color=0:0a7ea4,50:1A73E8,100:326CE5&height=120&section=footer
```

## Tech-stack badges (shields.io)

The badges in the README use `style=for-the-badge` for the tech stack and `style=flat-square` for status badges. Custom labels are added with `&labelColor=0b1f2a` so every badge shares the same dark navy left half — this is what makes them feel custom-made.

Examples:

| Tech         | URL fragment                                                                |
|--------------|------------------------------------------------------------------------------|
| Kubernetes   | `Kubernetes-326CE5?style=for-the-badge&logo=kubernetes&logoColor=white`     |
| Kustomize    | `Kustomize-1A73E8?style=for-the-badge&logo=kubernetes&logoColor=white`      |
| RabbitMQ     | `RabbitMQ-FF6600?style=for-the-badge&logo=rabbitmq&logoColor=white`         |
| PostgreSQL   | `PostgreSQL-336791?style=for-the-badge&logo=postgresql&logoColor=white`     |
| Elastic      | `Elastic_Stack-005571?style=for-the-badge&logo=elasticsearch&logoColor=white` |
| Cloudflared  | `Cloudflared-F38020?style=for-the-badge&logo=cloudflare&logoColor=white`    |

## Editing rules

- Keep the dark `labelColor` (`0b1f2a`) consistent across all badges in this repo.
- Pair every tech badge with a status badge below it (`for-the-badge` → `flat-square` line).
- If you add a new banner, document its URL in this file so the team can reuse it.
