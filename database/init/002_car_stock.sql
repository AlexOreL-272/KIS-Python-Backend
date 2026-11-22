create type core.car_operation_type as enum ('buy', 'sell');

create table core.car_stock (
    car_id bigint primary key references core.cars(id) on delete cascade,
    amount integer not null default 0 check (amount >= 0)
);

insert into core.car_stock (car_id, amount)
select id, 1 from core.cars;

create table core.car_operation_log (
    id bigserial primary key,
    car_id bigint not null,
    operation_type core.car_operation_type not null,
    amount_before integer not null,
    amount_after integer not null,
    occurred_at timestamptz not null default clock_timestamp(),
    constraint ck_car_operation_log_nonnegative check (
        amount_before >= 0 and amount_after >= 0
    )
);

create index ix_car_operation_log_car_id_id
on core.car_operation_log (car_id, id);

create function core.log_car_stock_change()
returns trigger
as $$
begin
    insert into core.car_operation_log (
        car_id, operation_type, amount_before, amount_after
    )
    values (
        new.car_id,
        case
            when new.amount < old.amount then 'buy'::core.car_operation_type
            else 'sell'::core.car_operation_type
        end,
        old.amount,
        new.amount
    );
    return new;
end;
$$ language plpgsql;

create trigger car_stock_change_log
after update of amount on core.car_stock
for each row
when (old.amount is distinct from new.amount)
execute function core.log_car_stock_change();
