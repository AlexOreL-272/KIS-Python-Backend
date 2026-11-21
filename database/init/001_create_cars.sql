begin;

create schema core;

create table core.cars (
    id bigserial primary key,
    name varchar(1024) not null,
    colour varchar(7) not null,
    price numeric(10, 2) not null,
    build_date date not null,
    created_at timestamptz not null default now(),

    constraint ck_cars_positive_price check (price > 0),
    constraint ck_cars_nonempty_name check (length(trim(name)) > 0),
    constraint ck_cars_colour check (colour ~ '^#[0-9a-fA-F]{6}$')
);

commit;
