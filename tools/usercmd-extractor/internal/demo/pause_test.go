package demo

import (
	st "github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/sendtables"
	"testing"
)

type observedRuleEntity struct {
	st.Entity
	values map[string]any
}

func (e observedRuleEntity) PropertyValue(name string) (st.PropertyValue, bool) {
	value, exists := e.values[name]
	return st.PropertyValue{Any: value}, exists
}

func TestPauseUsesObservedCS2RuleProperties(t *testing.T) {
	names := []string{"m_bGamePaused", "m_bMatchWaitingForResume", "m_bTerroristTimeOutActive", "m_bCTTimeOutActive", "m_bTechnicalTimeOut"}
	for _, scenario := range []string{"unpaused", "global", "technical", "missing", "untyped"} {
		t.Run(scenario, func(t *testing.T) {
			entity := observedRuleEntity{values: map[string]any{}}
			for _, name := range names {
				var value any = (scenario == "global" && name == "m_bGamePaused") || (scenario == "technical" && name == "m_bTechnicalTimeOut")
				exists := !(scenario == "missing" && name == "m_bTechnicalTimeOut")
				if scenario == "untyped" && name == "m_bTechnicalTimeOut" {
					value = nil
				}
				if exists {
					entity.values["m_pGameRules."+name] = value
				}
			}
			got := paused(entity)
			if scenario == "missing" || scenario == "untyped" {
				if got != nil {
					t.Fatal("missing property became known state")
				}
			} else if got == nil || *got != (scenario != "unpaused") {
				t.Fatalf("incorrect pause state: %v", got)
			}
		})
	}
	if paused(nil) != nil {
		t.Fatal("missing rules became known state")
	}
}

func TestMagazineCountPreservesRecordedZeroAndDoesNotSubtractOne(t *testing.T) {
	for _, count := range []uint32{0, 1, 20, 30} {
		entity := observedRuleEntity{values: map[string]any{"m_iClip1": count}}
		got := magazineAmmo(entity)
		if got == nil || *got != int32(count) {
			t.Fatalf("recorded count %d changed: %v", count, got)
		}
	}
	for _, value := range []any{nil, uint32(0xffffffff), int32(20), "20"} {
		if magazineAmmo(observedRuleEntity{values: map[string]any{"m_iClip1": value}}) != nil {
			t.Fatalf("unknown or unsupported count manufactured: %v", value)
		}
	}
	if magazineAmmo(nil) != nil || magazineAmmo(observedRuleEntity{values: map[string]any{}}) != nil {
		t.Fatal("absent count manufactured")
	}
}
