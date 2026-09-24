/** @odoo-module **/

import { useEffect, useState, xml } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SelectionField, selectionField } from "@web/views/fields/selection/selection_field";

class BookingTimeSlotField extends SelectionField {
    static template = xml`
        <t t-if="props.readonly">
            <span t-esc="string" t-att-raw-value="value" />
        </t>
        <t t-else="">
            <select class="o_input pe-3" t-on-change="onChange"
                t-on-click.stop="() =&gt; {}" t-att-id="props.id">
                <option t-att-selected="false === value"
                    t-att-value="stringify(false)"
                    t-esc="slotPlaceholder"
                    t-att-style="props.required &amp;&amp; options.length ? 'display:none' : ''" />
                <t t-foreach="options" t-as="option" t-key="option[0]">
                    <option t-att-selected="option[0] === value"
                        t-att-value="stringify(option[0])" t-esc="option[1]" />
                </t>
            </select>
            <div t-if="noAvailableSlots" class="text-muted small mt-1">
                No bookable times on this date. Choose another date within the permitted booking period.
            </div>
        </t>
    `;

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ allowed: [], loading: false, error: false });
        useEffect(
            () => {
                let cancelled = false;
                const resource = this.props.record.data.resource_id;
                const date = this.props.record.data.booking_date;
                this.state.allowed = [];
                this.state.loading = Boolean(resource && date);
                this.state.error = false;
                if (resource && date) {
                    this.orm.call("booking.booking", "get_available_time_slots", [
                        resource[0],
                        date,
                        this.props.record.resId || false,
                    ]).then((allowed) => {
                        if (!cancelled) {
                            this.state.allowed = allowed;
                            this.state.loading = false;
                            const selected = this.props.record.data[this.props.name];
                            if (this.props.record.isNew && selected && !allowed.includes(selected)) {
                                this.props.record.update({ [this.props.name]: false });
                            }
                        }
                    }).catch(() => {
                        if (!cancelled) {
                            this.state.allowed = [];
                            this.state.loading = false;
                            this.state.error = true;
                            this.notification.add(
                                "Could not load available booking times. Please try again.",
                                { type: "danger" }
                            );
                        }
                    });
                }
                return () => {
                    cancelled = true;
                };
            },
            () => [
                this.props.record.data.resource_id?.[0],
                this.props.record.data.booking_date,
                this.props.record.resId,
            ]
        );
    }

    get slotPlaceholder() {
        if (!this.props.record.data.resource_id || !this.props.record.data.booking_date) {
            return _t("Choose a room and date first");
        }
        if (this.state.loading) {
            return _t("Loading available times...");
        }
        if (this.state.error) {
            return _t("Could not load times");
        }
        if (!this.state.allowed.length) {
            return _t("No available times — choose another date");
        }
        return _t("Choose a time slot");
    }

    get noAvailableSlots() {
        return !this.props.readonly
            && this.props.record.data.resource_id
            && this.props.record.data.booking_date
            && !this.state.loading
            && !this.state.error
            && !this.state.allowed.length;
    }

    get options() {
        const options = super.options;
        const resource = this.props.record.data.resource_id;
        const date = this.props.record.data.booking_date;
        if (this.props.readonly || !resource || !date) {
            return options;
        }
        const selected = this.props.record.data[this.props.name];
        return options.filter(([value]) =>
            this.state.allowed.includes(value)
            || (!this.props.record.isNew && selected === value)
        );
    }
}

registry.category("fields").add("booking_available_time_slot", {
    ...selectionField,
    component: BookingTimeSlotField,
});
