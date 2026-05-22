Feature: Kill switch lifecycle
  REQ_TP_STR_003 / REQ_S_KS_007 / REQ_S_KS_009 — kill-switch and
  recovery scenarios SHALL be specified as Given/When/Then BDD
  scenarios so operator runbooks stay consistent with executable
  tests.

  Scenario: A KILL-severity trigger trips the kill switch
    Given an ACTIVE kill switch
    When a KILL-severity financial trigger is raised
    Then the kill switch state is KILL
    And must_halt returns True
    And one audit snapshot is recorded

  Scenario: A DEGRADE-severity trigger only degrades the kill switch
    Given an ACTIVE kill switch
    When a DEGRADE-severity strategy trigger is raised
    Then the kill switch state is DEGRADED
    And must_halt returns False

  Scenario: Recovery requires manual confirmation
    Given a KILL kill switch
    When recovery is requested with all recovery conditions met
    Then the kill switch state is ACTIVE
    And must_halt returns False

  Scenario: Recovery is rejected when conditions are unmet
    Given a KILL kill switch
    When recovery is requested with at least one condition unmet
    Then recovery returns an Err
    And the kill switch state is KILL
